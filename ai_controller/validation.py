"""代码质量验证模块 —— 自动对改动文件进行语法检查和测试。

提供 `run_validation` 作为统一入口，整合了：
1. py_compile 语法检查（对改动的 .py 文件逐文件编译）
2. pytest 测试（如果项目有测试配置则自动运行）
"""

import os
import re
import sys
import subprocess
import logging
from pathlib import Path


logger = logging.getLogger(__name__)


PYTEST_TIMEOUT = 60
"""pytest 单次运行默认超时（秒）。"""


# ─── 辅助: 检查项目是否有测试 ────────────────────────────────────────

def has_tests(target_dir: str) -> bool:
    """检查目标目录是否有可运行的测试。

    依次检查:
      1. tests/ 目录是否存在
      2. pytest.ini 文件是否存在
      3. pyproject.toml 中是否包含 [tool.pytest.ini_options]
    """
    target_path = Path(target_dir)

    if (target_path / "tests").is_dir():
        return True
    if (target_path / "pytest.ini").is_file():
        return True

    pyproject = target_path / "pyproject.toml"
    if pyproject.is_file():
        try:
            content = pyproject.read_text(encoding="utf-8")
            if "[tool.pytest.ini_options]" in content:
                return True
        except Exception:
            pass

    return False


# ─── py_compile 语法检查 ─────────────────────────────────────────────

def run_py_compile(target_dir: str, changed_files: list[str]) -> list[tuple[str, str]]:
    """对改动的 .py 文件逐个执行 python -m py_compile 语法检查。

    Args:
        target_dir: 项目根目录（用于拼接绝对路径）
        changed_files: 本轮改动的文件列表（相对路径）

    Returns:
        (file, error_message) 列表，空列表表示全部通过
    """
    errors: list[tuple[str, str]] = []

    for filepath in changed_files:
        if not filepath.endswith(".py"):
            continue
        full_path = Path(target_dir) / filepath
        if not full_path.is_file():
            continue

        try:
            proc = subprocess.run(
                [sys.executable, "-m", "py_compile", str(full_path)],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode != 0:
                msg = (proc.stderr or proc.stdout or "").strip()
                if not msg:
                    msg = f"py_compile 退出码 {proc.returncode}"
                errors.append((filepath, msg))
        except subprocess.TimeoutExpired:
            errors.append((filepath, "py_compile 超时 (30s)"))
        except Exception as e:
            errors.append((filepath, str(e)))

    return errors


# ─── pytest 测试 ──────────────────────────────────────────────────────

def run_pytest(target_dir: str, timeout: int = PYTEST_TIMEOUT) -> dict:
    """运行 pytest 测试（快速模式：-x --tb=short -q）。

    Args:
        target_dir: 项目根目录（作为 cwd）
        timeout: 超时秒数

    Returns:
        dict with keys:
            success:  bool   — 测试是否全部通过
            output:   str    — 合并的 stdout + stderr
            passed:   int    — 通过的测试数（粗略，失败时可能为 0）
            failed:   int    — 失败的测试数
            error:    str    — 异常/超时/未安装时的错误描述
    """
    result: dict = {
        "success": False,
        "output": "",
        "passed": 0,
        "failed": 0,
        "error": "",
    }

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-x", "--tb=short", "-q"],
            cwd=target_dir,
            capture_output=True, text=True, timeout=timeout,
        )

        output = proc.stdout or ""
        stderr = proc.stderr or ""
        result["output"] = output + ("\n" + stderr if stderr else "")

        # 从 pytest -q 输出中解析计数
        # 输出格式示例: ".F..                                                  [100%]"
        # 最后一行: "1 failed, 3 passed in 0.12s"
        # 或: "3 passed in 0.12s"
        # 或: "no tests ran"
        for line in output.splitlines():
            line = line.strip().lower()
            if "passed" in line or "failed" in line:
                # 尝试解析 "N passed" 和 "M failed"
                passed_m = re.search(r"(\d+)\s+passed", line)
                failed_m = re.search(r"(\d+)\s+failed", line)
                if passed_m:
                    result["passed"] = int(passed_m.group(1))
                if failed_m:
                    result["failed"] = int(failed_m.group(1))
                break

        if proc.returncode == 0:
            result["success"] = True
        elif proc.returncode == 1:
            # 有测试失败
            result["success"] = False
        elif proc.returncode == 5:
            # 未发现测试 — 不是错误
            result["success"] = True
            result["error"] = "未发现任何测试"
        else:
            result["error"] = f"pytest 异常退出 (code={proc.returncode})"

        return result

    except subprocess.TimeoutExpired:
        result["error"] = f"pytest 超时 ({timeout} 秒)"
        return result
    except FileNotFoundError:
        result["error"] = "pytest 未安装"
        return result
    except Exception as e:
        result["error"] = str(e)
        return result


# ─── 统一入口 ─────────────────────────────────────────────────────────

def run_validation(
    target_dir: str,
    changed_files: list[str],
    run_tests: bool = True,
) -> dict:
    """对改动文件执行质量验证。

    流程:
      1. 对每个改动的 .py 文件执行 py_compile 语法检查
      2. 如果项目有测试配置且 run_tests=True，运行 pytest

    Args:
        target_dir: 项目根目录
        changed_files: 本轮改动的文件列表（相对路径）
        run_tests: 是否尝试运行 pytest

    Returns:
        dict with keys:
            success:           bool          — 所有验证项通过
            has_tests:         bool          — 项目是否存在测试
            py_compile_errors: list[tuple]   — 语法错误列表
            test_result:       dict | None   — pytest 结果（None 表示未运行）
    """
    result: dict = {
        "success": True,
        "has_tests": False,
        "py_compile_errors": [],
        "test_result": None,
    }

    # 1. py_compile 语法检查
    py_errors = run_py_compile(target_dir, changed_files)
    result["py_compile_errors"] = py_errors
    if py_errors:
        result["success"] = False
        logger.warning(
            f"py_compile 检查发现 {len(py_errors)} 个语法错误: "
            f"{', '.join(f[0] for f in py_errors[:5])}"
            f"{' ...' if len(py_errors) > 5 else ''}"
        )

    # 2. pytest 测试
    if run_tests:
        result["has_tests"] = has_tests(target_dir)
        if result["has_tests"]:
            test_result = run_pytest(target_dir)
            result["test_result"] = test_result

            if not test_result["success"]:
                result["success"] = False
                logger.warning(
                    f"pytest 未通过: {test_result.get('error', '') or '测试失败'}"
                )

            if test_result.get("passed", 0) > 0:
                logger.info(f"pytest 通过: {test_result['passed']} 个")
            if test_result.get("failed", 0) > 0:
                logger.warning(f"pytest 失败: {test_result['failed']} 个")
            if test_result.get("error"):
                logger.warning(f"pytest 错误: {test_result['error']}")

    return result
