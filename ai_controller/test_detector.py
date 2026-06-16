"""测试命令自动发现模块。

提供 `detect_test_command()` 函数，根据项目目录中的配置文件
自动推断适用于该项目的测试命令。

支持的框架（按优先级从高到低）:
  1. --test-command CLI 参数手动指定 (最高优先级)
  2. pytest.ini → pytest
  3. pyproject.toml 含 [tool.pytest.ini_options] → pytest
  4. tox.ini → tox
  5. package.json 含 scripts.test → npm/yarn/pnpm test
  6. Cargo.toml → cargo test
  7. go.mod → go test ./...
  8. build.sbt → sbt test
  9. deno.json / deno.jsonc → deno test
  10. Makefile 含 test target → make test
"""

import os
import re
import json
import logging
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


def _get_python_cmd() -> str:
    """获取当前 Python 可执行文件名（python3 或 python）。"""
    import sys
    return sys.executable


def _check_pytest_ini(target_path: Path) -> Optional[list[str]]:
    """检测 pytest.ini。"""
    if (target_path / "pytest.ini").is_file():
        return [_get_python_cmd(), "-m", "pytest", "-x", "-q"]
    return None


def _check_pyproject_toml(target_path: Path) -> Optional[list[str]]:
    """检测 pyproject.toml 是否包含 [tool.pytest.ini_options]。"""
    pyproject = target_path / "pyproject.toml"
    if pyproject.is_file():
        try:
            content = pyproject.read_text(encoding="utf-8", errors="replace")
            if "[tool.pytest.ini_options]" in content:
                return [_get_python_cmd(), "-m", "pytest", "-x", "-q"]
        except Exception:
            pass
    return None


def _check_tox_ini(target_path: Path) -> Optional[list[str]]:
    """检测 tox.ini。"""
    if (target_path / "tox.ini").is_file():
        return ["tox"]
    return None


def _check_package_json(target_path: Path) -> Optional[list[str]]:
    """检测 package.json 是否包含 scripts.test。"""
    pkg = target_path / "package.json"
    if not pkg.is_file():
        return None
    try:
        data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
        if "scripts" in data and "test" in data["scripts"]:
            # 检测包管理器偏好
            if (target_path / "pnpm-lock.yaml").is_file():
                return ["pnpm", "test"]
            if (target_path / "yarn.lock").is_file():
                return ["yarn", "test"]
            return ["npm", "test"]
    except Exception:
        pass
    return None


def _check_cargo_toml(target_path: Path) -> Optional[list[str]]:
    """检测 Cargo.toml。"""
    if (target_path / "Cargo.toml").is_file():
        return ["cargo", "test"]
    return None


def _check_go_mod(target_path: Path) -> Optional[list[str]]:
    """检测 go.mod。"""
    if (target_path / "go.mod").is_file():
        return ["go", "test", "./..."]
    return None


def _check_build_sbt(target_path: Path) -> Optional[list[str]]:
    """检测 build.sbt (Scala/SBT 项目)。"""
    if (target_path / "build.sbt").is_file():
        return ["sbt", "test"]
    return None


def _check_deno(target_path: Path) -> Optional[list[str]]:
    """检测 deno.json / deno.jsonc。"""
    if (target_path / "deno.json").is_file() or (target_path / "deno.jsonc").is_file():
        return ["deno", "test"]
    return None


def _check_makefile(target_path: Path) -> Optional[list[str]]:
    """检测 Makefile 是否包含 test target。"""
    makefile = target_path / "Makefile"
    if not makefile.is_file():
        makefile = target_path / "makefile"
    if not makefile.is_file():
        return None
    try:
        content = makefile.read_text(encoding="utf-8", errors="replace")
        # 简单检测: 查找 "test:" 开头的 target (忽略注释和变量赋值)
        if re.search(r"^test:", content, re.MULTILINE):
            return ["make", "test"]
    except Exception:
        pass
    return None


# 检测策略表: (名称, 检测函数)
DETECTORS: list[tuple[str, callable]] = [
    ("pytest.ini", _check_pytest_ini),
    ("pyproject.toml (pytest)", _check_pyproject_toml),
    ("tox.ini", _check_tox_ini),
    ("package.json (test script)", _check_package_json),
    ("Cargo.toml", _check_cargo_toml),
    ("go.mod", _check_go_mod),
    ("build.sbt", _check_build_sbt),
    ("deno.json/deno.jsonc", _check_deno),
    ("Makefile (test target)", _check_makefile),
]


def detect_test_command(
    target_dir: str,
    override_command: Optional[str] = None,
) -> Optional[list[str]]:
    """自动发现项目的测试命令。

    流程:
      1. 如果 override_command 有值，用 shlex 拆分后直接返回
      2. 按 DETECTORS 表依次检测，返回第一个匹配的命令
      3. 全部不匹配返回 None

    Args:
        target_dir: 项目根目录
        override_command: 手动指定的测试命令字符串（如 "npm test"）

    Returns:
        测试命令列表（如 ["python", "-m", "pytest", "-x", "-q"]），
        或 None（无法检测到测试命令）
    """
    target_path = Path(target_dir).resolve()

    # 1. 手动指定命令（最高优先级）
    if override_command:
        import shlex
        cmd_parts = shlex.split(override_command)
        logger.info(
            f"使用手动指定的测试命令: {override_command}"
        )
        return cmd_parts

    # 2. 逐一检测
    for name, detector in DETECTORS:
        try:
            result = detector(target_path)
            if result is not None:
                logger.info(
                    f"自动检测到测试框架 ({name}): {' '.join(result)}"
                )
                return result
        except Exception as e:
            logger.debug(f"检测 {name} 时出错: {e}")
            continue

    logger.info("未检测到任何已知的测试框架（返回 None）")
    return None
