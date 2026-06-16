"""代码质量验证与回滚模块。

提供底层工具函数：
1. run_py_compile — 对改动文件进行语法检查
2. run_pytest — 运行 pytest 测试
3. has_tests — 检测项目是否有测试配置
4. rollback_and_record — 精确回滚本轮改动并记录到 CHANGELOG
"""

import os
import re
import sys
import subprocess
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional


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


# ─── 运行自定义测试命令 ─────────────────────────────────────────────

def run_test_command(
    target_dir: str,
    cmd: list[str],
    timeout: int = 120,
) -> dict:
    """运行任意测试命令并收集结果。

    Args:
        target_dir: 项目根目录（作为 cwd）
        cmd: 命令列表，如 ["npm", "test"]
        timeout: 超时秒数（默认 120s，比 pytest 更长以适应编译型语言）

    Returns:
        dict with keys:
            success:  bool   — 命令是否以 0 退出
            output:   str    — 合并的 stdout + stderr
            error:    str    — 异常/超时/未找到时的错误描述
    """
    result: dict = {
        "success": False,
        "output": "",
        "error": "",
    }

    try:
        proc = subprocess.run(
            cmd,
            cwd=target_dir,
            capture_output=True, text=True, timeout=timeout,
        )
        output = proc.stdout or ""
        stderr = proc.stderr or ""
        result["output"] = output + ("\n" + stderr if stderr else "")

        if proc.returncode == 0:
            result["success"] = True
        else:
            result["error"] = f"测试命令退出码 {proc.returncode}"

        return result

    except subprocess.TimeoutExpired:
        result["error"] = f"测试命令超时 ({timeout} 秒)"
        return result
    except FileNotFoundError:
        result["error"] = f"测试命令未找到: {' '.join(cmd)}"
        return result
    except Exception as e:
        result["error"] = str(e)
        return result


# ─── 精确回滚 + 记录 ─────────────────────────────────────────────────

LOG_FILE = "AI-CHANGELOG.md"


def rollback_and_record(
    target_dir: str,
    changed_files: list[str],
    test_command: list[str],
    test_output: str,
    round_num: int,
    summary: str,
    pre_snapshot: Optional[dict] = None,
) -> None:
    """精确回滚本轮改动并记录到 CHANGELOG。

    回滚策略:
      1. git checkout HEAD -- <modified_files> 恢复已跟踪文件的修改/删除
      2. 删除本轮新增的已跟踪文件（diff-filter=A）
      3. 对比改动前快照，删除本轮新增的未跟踪文件

    Args:
        target_dir: 项目根目录
        changed_files: 本轮改动文件列表
        test_command: 测试命令列表
        test_output: 测试完整输出
        round_num: 当前轮次
        summary: 本轮改动说明
        pre_snapshot: 改动前的文件状态快照，包含:
            - tracked_files: git ls-files 输出
            - untracked_files: git ls-files --others --exclude-standard 输出
            - staged_files: git diff --cached --name-only 输出
    """
    target_path = Path(target_dir)
    git_repo = (target_path / ".git").is_dir()

    if not git_repo:
        logger.warning("非 git 仓库，无法执行精确回滚")
        return

    try:
        # ── 1a. 获取已跟踪文件的修改/删除列表 ──
        proc_modified = subprocess.run(
            ["git", "-C", target_dir, "diff", "--name-only", "--diff-filter=MRD", "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
        modified_files = [f for f in proc_modified.stdout.splitlines() if f.strip()]

        # ── 1b. 获取本轮新增的已跟踪文件 ──
        proc_added = subprocess.run(
            ["git", "-C", target_dir, "diff", "--name-only", "--diff-filter=A", "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
        added_tracked_files = [f for f in proc_added.stdout.splitlines() if f.strip()]

        # ── 1c. 回滚已跟踪文件的修改/删除 ──
        if modified_files:
            subprocess.run(
                ["git", "-C", target_dir, "checkout", "HEAD", "--"] + modified_files,
                capture_output=True, timeout=30,
            )
            logger.info(f"已回滚 {len(modified_files)} 个已跟踪文件的修改")

        # ── 1d. 删除本轮新增的已跟踪文件 ──
        for f in added_tracked_files:
            full_path = target_path / f
            if full_path.exists():
                full_path.unlink()
                logger.info(f"已删除新增文件: {f}")

        # ── 1e. 对比快照，删除本轮新增的未跟踪文件 ──
        if pre_snapshot:
            before_untracked = set(pre_snapshot.get("untracked_files", []))
            # 获取当前未跟踪文件
            proc_current_untracked = subprocess.run(
                ["git", "-C", target_dir, "ls-files", "--others", "--exclude-standard"],
                capture_output=True, text=True, timeout=30,
            )
            current_untracked = set(
                f for f in proc_current_untracked.stdout.splitlines() if f.strip()
            )
            # 新增的未跟踪文件 = 当前 - 改动前
            new_untracked = current_untracked - before_untracked
            for f in new_untracked:
                full_path = target_path / f
                if full_path.exists():
                    full_path.unlink()
                    logger.info(f"已删除新增未跟踪文件: {f}")

        # ── 2. 记录到 AI-CHANGELOG.md ──
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cmd_str = " ".join(test_command)
        # 截断输出到 300 字符
        output_preview = test_output[:300].rstrip()
        if len(test_output) > 300:
            output_preview += "..."

        log_lines = [
            "",
            f"> **\u26a0\ufe0f 测试失败，已自动回滚** (Round {round_num} - {ts})",
            f">",
            f"> - 改动说明: {summary}",
            f"> - 测试命令: `{cmd_str}`",
            f"> - 回滚文件: {len(modified_files) + len(added_tracked_files) + (len(new_untracked) if pre_snapshot else 0)} 个",
            f"> - 测试输出预览:",
        ]
        for line in output_preview.split("\n"):
            log_lines.append(f">   {line}")
        log_lines.append("")
        log_lines.append("")

        log_path = target_path / LOG_FILE
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines))

        logger.warning(f"测试失败，已自动回滚 {len(modified_files) + len(added_tracked_files)} 个文件")
        print(f"  \u26a0\ufe0f 测试失败，已自动回滚本轮改动 (Round {round_num})")

    except Exception as e:
        logger.warning(f"精确回滚失败: {e}")
        print(f"  \u26a0\ufe0f 回滚失败: {e}")
