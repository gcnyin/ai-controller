"""Git 操作 —— 仓库检测、变动检测、自动提交等。"""

import os
import subprocess
from pathlib import Path

from .logger import get_logger, LOG_FILE
from .backup import BACKUP_DIR_NAME


def is_git_repo(target_dir: str) -> bool:
    return (Path(target_dir) / ".git").is_dir()


def has_changes(target_dir: str) -> bool:
    """检查工作区是否有未提交的改动（含暂存和未暂存）。

    使用 git status --porcelain 一步检测所有未提交变更：
    - 未暂存改动（工作区 vs 暂存区）
    - 已暂存改动（暂存区 vs HEAD）
    - 未跟踪文件
    避免 git diff --quiet 只能检测未暂存改动的局限。
    """
    try:
        r = subprocess.run(
            ["git", "-C", target_dir, "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        )
        return bool(r.stdout.strip())
    except Exception:
        return False


def git_commit(target_dir: str, round_num: int):
    """自动提交"""
    try:
        subprocess.run(
            ["git", "-C", target_dir, "add", "-A"],
            capture_output=True, timeout=30,
        )
        msg = f"[AI-Controller] Round {round_num}"
        subprocess.run(
            ["git", "-C", target_dir, "commit", "-m", msg, "--allow-empty"],
            capture_output=True, timeout=30,
        )
    except Exception:
        # git 提交失败不应中断主流程，自动提交是辅助功能，可忽略
        get_logger().warning(f"Git 提交失败（Round {round_num}），继续执行")


def get_changed_files(target_dir: str, since_ts: float = 0) -> list[str]:
    """获取本轮改动的文件列表。

    优先使用 git status --porcelain（精确且快速）。
    如果没有 git 仓库，则回退到文件系统时间戳比较。

    Args:
        target_dir: 目标目录路径
        since_ts: fallback 模式下用于比较的时间戳，凡 mtime > since_ts 的文件视为改动过
    """
    target_path = Path(target_dir)

    # 控制器自身的管理文件，不应计入项目改动
    controller_files = {LOG_FILE}

    # ── 优先：git 仓库 ──
    if (target_path / ".git").is_dir():
        try:
            r = subprocess.run(
                ["git", "-C", target_dir, "status", "--porcelain"],
                capture_output=True, text=True, timeout=10,
            )
            files = []
            for line in r.stdout.splitlines():
                if not line.strip():
                    continue
                # git status --porcelain: "XY filename" -- X=staged, Y=unstaged
                # 取第 4 个字符开始的路径（处理重命名时是 "R  old -> new"）
                path = line[3:].strip()
                # 处理重命名格式: "old -> new"
                if " -> " in path:
                    path = path.split(" -> ")[-1]
                if not path:
                    continue
                # 过滤控制器自身的管理文件
                if path in controller_files:
                    continue
                if path.startswith(BACKUP_DIR_NAME + "/") or path == BACKUP_DIR_NAME:
                    continue
                files.append(path)
            return files
        except Exception:
            # git diff-index 失败时回退到文件时间戳方案，可忽略
            get_logger().warning("git diff-index 失败，回退到文件时间戳方案")

    # ── 回退：基于文件系统时间戳（用于非 git 目录） ──
    if since_ts > 0:
        changed = []
        # 需要跳过的目录（不遍历）
        skip_dirs = {BACKUP_DIR_NAME, ".git", "__pycache__", ".venv", "venv",
                     "node_modules", "dist", "build", ".next"}
        for root, dirs, files in os.walk(target_dir, topdown=True):
            # 过滤要跳过的目录
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".ai-controller-")]
            for f in files:
                # 过滤控制器自身的管理文件
                if f == LOG_FILE:
                    continue
                fp = os.path.join(root, f)
                try:
                    if os.path.getmtime(fp) > since_ts:
                        rel = os.path.relpath(fp, target_dir)
                        changed.append(rel)
                except OSError:
                    # 文件可能已被删除或无权限读取，跳过该文件
                    get_logger().warning(f"无法读取文件时间戳，已跳过: {fp}")
        return sorted(changed)

    return []


def get_git_diff_summary(target_dir: str) -> str:
    """获取 git diff 的简短摘要作为 fallback"""
    try:
        r = subprocess.run(
            ["git", "-C", target_dir, "diff", "--stat"],
            capture_output=True, text=True, timeout=10,
        )
        stat = r.stdout.strip()
        if stat:
            lines = stat.split("\n")
            # 最后一行是 summary: "X files changed, Y insertions(+), Z deletions(-)"
            last = lines[-1] if lines else stat
            return f"Git diff: {last}"
    except Exception:
        # git diff 失败时静默返回空字符串作为 fallback，可忽略
        get_logger().warning("git diff --stat 失败，无法获取 diff 摘要")
    return ""
