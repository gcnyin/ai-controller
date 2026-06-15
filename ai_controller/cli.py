"""CLI 入口与主循环 —— 命令行解析、迭代调度、日志记录。"""

import os
import re
import sys
import time
import shlex
import shutil
import argparse
import textwrap
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, List

from . import C, cprint
from .logger import get_logger, setup_logger, LOG_FILE
from .agent import AGENTS, call_agent
from .prompts import TASK_PROMPT, build_task_prompt, PLAN_PROMPT
from .tasks import (
    TASK_FILE,
    generate_task_list,
    save_task_list,
    load_task_list,
    mark_task_done,
    get_next_pending_task,
)
from .backup import BACKUP_DIR_NAME, backup_all, cleanup_old_backups
from .git_ops import (
    is_git_repo,
    has_changes,
    git_commit,
    get_changed_files,
    get_git_diff_summary,
)


# ─── 文件过滤参数 ─────────────────────────────────────────────────────

def build_ext_filter_arg(agent: str, exts: Optional[set]) -> Optional[str]:
    """构建文件过滤参数。目前通过 prompt 形式告知 agent。"""
    if not exts:
        return None
    ext_list = ", ".join(sorted(exts))
    return f"只处理 {ext_list} 文件，忽略其他文件类型。"


def check_ext_filter(changed_files: list[str], allowed_ext: Optional[set]) -> tuple[list[str], list[str]]:
    """将改动文件按后缀过滤，分为匹配和不匹配两组。

    如果 allowed_ext 为 None，所有文件都视为匹配。
    匹配规则：文件后缀必须在 allowed_ext 集合中（含前置点，如 {'.py', '.ts'}）。

    Returns:
        (matching_files, non_matching_files) — 匹配的文件列表和不匹配的文件列表
    """
    if not allowed_ext:
        return changed_files, []

    matching = []
    non_matching = []
    for f in changed_files:
        _, ext = os.path.splitext(f)
        if ext in allowed_ext:
            matching.append(f)
        else:
            non_matching.append(f)
    return matching, non_matching


# ─── 日志记录辅助 ──────────────────────────────────────────────────────

def extract_model_hint(agent_args: Optional[list]) -> str:
    """从 agent 额外参数中提取模型信息，用于 changelog 头部显示。

    支持 --model <name> 和 -m <name> 两种写法。
    如果同时出现多个，取最后一个。
    """
    if not agent_args:
        return ""
    # 迭代查找 --model / -m，取最后出现的值
    hint = ""
    skip_next = False
    for i, arg in enumerate(agent_args):
        if skip_next:
            skip_next = False
            continue
        if arg in ("--model", "-m") and i + 1 < len(agent_args):
            hint = agent_args[i + 1]
            skip_next = True
            continue
        # 处理 --model=xxx 写法
        if arg.startswith("--model=") or arg.startswith("-m="):
            hint = arg.split("=", 1)[1]
    return hint


def init_log(target_dir: str, agent: str, model_hint: str = ""):
    """初始化 changelog 文件和双输出 logger。

    如果 AI-CHANGELOG.md 已存在则追加模式（不覆盖），
    同时初始化 logger 使其同时输出到控制台（带颜色）和 ai-controller.log 文件。
    """
    log_path = Path(target_dir) / LOG_FILE
    model_str = f" ({model_hint})" if model_hint else ""
    if not log_path.exists():
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_path.write_text(
            f"# AI 自迭代改动记录\n\n"
            f"- 开始时间: {ts}\n"
            f"- Agent: {agent}{model_str}\n"
            f"- 目标目录: {target_dir}\n\n"
            f"---\n\n",
            encoding="utf-8",
        )

    # 设置双输出 logger（控制台 + 文件）
    setup_logger(target_dir)
    logger = get_logger()
    logger.info(f"AI 自迭代控制器启动 — Agent: {agent}{model_str}, 目标: {target_dir}")


def parse_changelog_for_resume(target_dir: str) -> Optional[Tuple[int, str]]:
    """解析 AI-CHANGELOG.md，提取最后一轮的轮次号和改动说明。

    用于 --resume 模式：读取已有的 changelog，找到最后完成的轮次，
    从下一轮继续迭代，并将上一轮的改动说明作为上下文传入。

    Returns:
        (last_round_num, last_summary) 或 None（changelog 不存在或无法解析）
    """
    log_path = Path(target_dir) / LOG_FILE
    if not log_path.is_file():
        return None

    try:
        content = log_path.read_text(encoding="utf-8")
    except Exception:
        return None

    # 匹配 "## Round N — YYYY-MM-DD HH:MM:SS" 后面跟着 "**改动说明**: ..." 或 "改动说明: ..."
    # 使用 DOTALL 以确保改动说明跨行时也能正确捕获
    pattern = r'## Round (\d+) — [^\n]*\n+\n\*{0,2}改动说明\*{0,2}[:：]\s*(.+?)(?:\n\n|\n##|\n\*|$)'
    matches = re.findall(pattern, content, re.DOTALL)

    if not matches:
        return None

    # 取最后一组匹配
    last_round_str, last_summary = matches[-1]
    try:
        last_round = int(last_round_str)
    except ValueError:
        return None

    return last_round, last_summary.strip()


def write_round_log(
    target_dir: str,
    round_num: int,
    summary: str,
    changed_files: list[str],
    elapsed: float,
):
    """追加一轮的改动记录到 changelog"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"## Round {round_num} — {ts}",
        "",
        f"改动说明: {summary}",
        "",
    ]
    if changed_files:
        lines.append(f"改动文件 ({len(changed_files)} 个):")
        for f in changed_files:
            lines.append(f"- `{f}`")
    else:
        lines.append("改动文件: 无（本轮无代码变更）")

    lines.append("")
    lines.append(f"*耗时 {elapsed:.1f}s*")
    lines.append("")

    log_path = Path(target_dir) / LOG_FILE
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # 同时输出到 logger（控制台 + 日志文件）
    logger = get_logger()
    file_list = ", ".join(changed_files[:8]) if changed_files else "无"
    if len(changed_files) > 8:
        file_list += f" ...共{len(changed_files)}个"
    logger.info(f"Round #{round_num} | 耗时 {elapsed:.1f}s | {summary} | 文件: {file_list}")


# ─── 单轮执行 ─────────────────────────────────────────────────────────

def _execute_single_round(
    target_dir: str,
    agent: str,
    round_num: int,
    prompt: str,
    allowed_ext: Optional[set],
    no_backup: bool,
    no_git: bool,
    timeout: int,
    agent_args: Optional[list],
    ext_filter: Optional[str],
    keep_backups: int,
    summary_prefix: str = "",
    error_label: str = "Agent 返回异常",
) -> dict:
    """执行单轮迭代的核心逻辑：备份、调用 Agent、检测改动、过滤、记录日志、Git 提交。

    此函数封装了 run_loop（任务模式）和 _run_legacy_loop（逐轮模式）共用的
    重复代码，两个循环只保留差异化的调度逻辑（prompt 构建、任务标记、prev_summary 维护）。

    Args:
        summary_prefix: 写入 changelog 时加在 summary 前面的前缀（如 "[任务#1] "）
        error_label: Agent 异常且无改动时的日志描述（如 "跳过任务 #1" 或 "等待后继续..."）

    Returns:
        dict with keys:
            success: Agent 是否正常退出
            summary: 本轮改动说明（可能已追加后缀过滤警告）
            changed_files: 改动的文件列表
            elapsed: Agent 执行耗时（秒）
            has_diff: 是否有文件改动（Agent 异常且无改动时为 False）
    """
    # ── 备份 ──
    if not no_backup:
        backup_folder = backup_all(target_dir, round_num)
        if backup_folder:
            cprint(f"  💾 已备份到: {backup_folder}", C.GREEN)
        if keep_backups > 0:
            cleanup_old_backups(target_dir, keep_backups)

    git_repo = is_git_repo(target_dir) and not no_git
    before_ts = time.time()

    # ── 调用 Agent ──
    success, summary, raw_output, elapsed = call_agent(
        agent, prompt, target_dir, ext_filter, timeout, agent_args,
        quiet=True,
    )

    print()

    # ── 检测文件改动 ──
    changed_files = get_changed_files(target_dir, before_ts)
    has_diff = bool(changed_files)

    # ── Agent 异常且无改动：记录日志后返回 ──
    if not success and not has_diff:
        get_logger().warning(f"Agent 返回异常，{error_label}")
        write_round_log(target_dir, round_num, f"{summary_prefix}{summary}", [], elapsed)
        return {"success": False, "summary": summary, "changed_files": [], "elapsed": elapsed, "has_diff": False}

    # ── Agent 异常但有改动：警告后继续处理 ──
    if not success and has_diff:
        get_logger().warning("Agent 返回异常但仍有文件改动，继续处理...")

    # ── 扩展名过滤与日志记录 ──
    if has_diff:
        filtered_files, bad_files = check_ext_filter(changed_files, allowed_ext)
        if bad_files:
            get_logger().warning(
                f"Agent 修改了 {len(bad_files)} 个非目标后缀文件: "
                f"{', '.join(bad_files[:5])}"
                f"{' ...' if len(bad_files) > 5 else ''}"
            )
            suffix_note = f" [注意: Agent 同时修改了 {len(bad_files)} 个非目标后缀文件]"
            summary = summary + suffix_note

        # ── 记录 changelog ──
        write_round_log(target_dir, round_num, f"{summary_prefix}{summary}", changed_files, elapsed)

        # ── Git 提交 ──
        if git_repo:
            diff_stat = get_git_diff_summary(target_dir)
            git_commit(target_dir, round_num)
            if diff_stat:
                cprint(f"  ✓ 改动: {diff_stat}", C.GREEN)
            else:
                cprint(f"  ✓ 已提交改动", C.GREEN)
        else:
            cprint(f"  ✓ 修改了 {len(changed_files)} 个文件", C.GREEN)

        cprint(f"  📝 {summary}", C.MAGENTA)
        cprint(f"  📄 {', '.join(changed_files[:5])}"
               f"{' ...' if len(changed_files) > 5 else ''}", C.GREEN)
    else:
        get_logger().info(f"本轮无文件改动 — {summary}")
        write_round_log(target_dir, round_num, f"{summary_prefix}{summary}", [], elapsed)

    return {"success": success, "summary": summary, "changed_files": changed_files, "elapsed": elapsed, "has_diff": has_diff}


# ─── 主循环 ────────────────────────────────────────────────────────────

def run_loop(
    target_dir: str,
    agent: str,
    max_rounds: int = 10,
    allowed_ext: Optional[set] = None,
    no_backup: bool = False,
    no_git: bool = False,
    sleep_between: float = 2.0,
    timeout: int = 600,
    agent_args: Optional[list] = None,
    resume: bool = False,
    keep_backups: int = 0,
    no_plan: bool = False,
    dry_run: bool = False,
):
    print()
    cprint("╔══════════════════════════════════════════╗", C.CYAN)
    cprint("║      AI 自迭代控制器 v2.1               ║", C.CYAN)
    cprint("╚══════════════════════════════════════════╝", C.CYAN)
    print()
    cprint(f"  目标目录 : {target_dir}", C.BOLD)
    cprint(f"  Agent    : {agent}", C.BOLD)
    cprint(f"  超时     : {timeout}s", C.BOLD)
    cprint(f"  最大轮次 : {'无限' if max_rounds == 0 else max_rounds}", C.BOLD)
    if allowed_ext:
        cprint(f"  文件过滤 : {', '.join(sorted(allowed_ext))}", C.BOLD)
    if not no_backup:
        cprint(f"  备份目录 : {BACKUP_DIR_NAME}/", C.BOLD)
        if keep_backups > 0:
            cprint(f"  备份保留 : 最近 {keep_backups} 个", C.BOLD)
    if is_git_repo(target_dir) and not no_git:
        cprint(f"  Git      : 自动 commit", C.BOLD)
    if no_plan:
        cprint(f"  模式     : 逐轮模式（无任务列表）", C.BOLD)
    if dry_run:
        cprint(f"  模式     : 预览模式（不实际修改任何文件）", C.YELLOW + C.BOLD)
    print()

    ext_filter = build_ext_filter_arg(agent, allowed_ext)
    model_hint = extract_model_hint(agent_args)
    init_log(target_dir, agent, model_hint)
    if model_hint:
        cprint(f"  模型     : {model_hint}", C.BOLD)

    # 检查工作区是否有未提交的改动，如有则警告用户
    if is_git_repo(target_dir) and not no_git and has_changes(target_dir):
        get_logger().warning("工作区存在未提交的改动，将与 AI 改动混合记录")

    # ─── 逐轮模式（--no-plan）：保持原有行为 ───
    if no_plan:
        _run_legacy_loop(
            target_dir, agent, max_rounds, allowed_ext,
            no_backup, no_git, sleep_between, timeout,
            agent_args, resume, keep_backups, ext_filter,
            dry_run=dry_run,
        )
        return

    # ─── 两阶段模式：先规划，再逐条执行 ───

    # 阶段 1：生成任务列表
    tasks = None
    if resume:
        tasks = load_task_list(target_dir)
        if tasks:
            pending = [t for t in tasks if t.get("status") != "done"]
            get_logger().info(f"恢复模式: 从任务列表恢复（已完成 {len(tasks) - len(pending)}/{len(tasks)} 个任务）")
            if not pending:
                get_logger().info("任务列表中所有任务已完成，退出。")
                return
        else:
            get_logger().warning("无法加载任务列表，将重新生成。")

    if tasks is None:
        if dry_run and Path(target_dir, TASK_FILE).is_file():
            # 预览模式：如果已存在任务列表文件则直接加载，避免重新调用 Agent
            tasks = load_task_list(target_dir)
            get_logger().info("预览模式: 加载已有任务列表，跳过规划阶段 Agent 调用")
        if tasks is None:
            tasks = generate_task_list(agent, target_dir, ext_filter, timeout, agent_args)
        if tasks is None:
            get_logger().warning("任务列表生成失败，回退到逐轮模式")
            _run_legacy_loop(
                target_dir, agent, max_rounds, allowed_ext,
                no_backup, no_git, sleep_between, timeout,
                agent_args, False, keep_backups, ext_filter,
                dry_run=dry_run,
            )
            return

        if len(tasks) == 0:
            get_logger().info("AI 评估后认为代码库已完善，无需改进")
            return

        save_task_list(target_dir, tasks)
        logger = get_logger()
        logger.info(f"任务列表已生成: {len(tasks)} 个任务，保存至 {TASK_FILE}")
        # 打印全部任务概览（同时输出到控制台和日志文件）
        logger.info(f"{'─' * 40}")
        logger.info(f"任务列表（共 {len(tasks)} 个）:")
        for t in tasks:
            logger.info(f"  #{t.get('id')} [{t.get('priority', '?')}] [{t.get('type', '')}] {t.get('title', '')}")
        logger.info(f"{'─' * 40}")

    # ─── 预览模式：打印任务执行计划后退出 ───
    if dry_run:
        _dry_run_task_loop(target_dir, agent, tasks, max_rounds, agent_args)
        return

    # 阶段 2：逐条执行任务
    round_num = parse_changelog_for_resume(target_dir)
    round_num = round_num[0] if round_num else 0
    consecutive_noops = 0

    while True:
        # 获取下一个待执行任务
        task = get_next_pending_task(target_dir)
        if task is None:
            get_logger().info("所有任务已完成！")
            break

        round_num += 1

        if max_rounds > 0 and round_num > max_rounds:
            pending_left = len([t for t in (load_task_list(target_dir) or []) if t.get("status") != "done"])
            get_logger().info(f"达到最大轮次 {max_rounds}（剩余 {pending_left} 个待执行任务），退出。")
            break

        if consecutive_noops >= 3:
            get_logger().info(f"连续 {consecutive_noops} 轮无改动，退出。")
            break

        tid = task.get("id", "?")
        title = task.get("title", "")

        cprint(f"\n{'─' * 55}", C.CYAN)
        cprint(f"  第 {round_num} 轮: 执行任务 #{tid} — {title}", C.BOLD + C.CYAN)
        cprint(f"{'─' * 55}", C.CYAN)

        # 构建任务 prompt 并执行单轮
        prompt = build_task_prompt(task)
        result = _execute_single_round(
            target_dir, agent, round_num, prompt, allowed_ext,
            no_backup, no_git, timeout, agent_args, ext_filter,
            keep_backups,
            summary_prefix=f"[任务#{tid}] ",
            error_label=f"跳过任务 #{tid}",
        )

        if not result["success"] and not result["has_diff"]:
            consecutive_noops += 1
            time.sleep(sleep_between)
            continue

        if result["has_diff"]:
            mark_task_done(target_dir, task["id"], round_num)
            consecutive_noops = 0
        else:
            # 无改动时也标记完成，避免死循环在同一个任务上
            mark_task_done(target_dir, task["id"], round_num)
            consecutive_noops += 1

        cprint(f"  ⏳ 等待 {sleep_between}s...", C.CYAN)
        time.sleep(sleep_between)


def _run_legacy_loop(
    target_dir: str,
    agent: str,
    max_rounds: int,
    allowed_ext: Optional[set],
    no_backup: bool,
    no_git: bool,
    sleep_between: float,
    timeout: int,
    agent_args: Optional[list],
    resume: bool,
    keep_backups: int,
    ext_filter: Optional[str],
    dry_run: bool = False,
):
    """原有的逐轮模式：每轮让 AI 自己选一件事来做。"""
    consecutive_noops = 0
    round_num = 0
    prev_summary = ""

    if resume:
        resume_info = parse_changelog_for_resume(target_dir)
        if resume_info is None:
            get_logger().warning("无法解析 changelog 中的进度信息，从头开始。")
            cprint("  ⚠ 无法解析 changelog 中的进度信息，从头开始。", C.YELLOW)
        else:
            last_round, last_summary = resume_info
            if max_rounds > 0 and last_round >= max_rounds:
                get_logger().info(f"上次已完成 {last_round}/{max_rounds} 轮，无需恢复。")
                return
            round_num = last_round
            prev_summary = last_summary
            get_logger().info(f"恢复模式: 从第 {last_round + 1} 轮继续（上次完成 {last_round} 轮）")
            cprint(f"  恢复模式 : 从第 {last_round + 1} 轮继续（上次完成 {last_round} 轮）", C.BOLD + C.CYAN)
            print()

    while True:
        round_num += 1

        if max_rounds > 0 and round_num > max_rounds:
            get_logger().info(f"达到最大轮次 {max_rounds}，退出。")
            cprint(f"\n✓ 达到最大轮次 {max_rounds}，退出。", C.GREEN)
            break

        if consecutive_noops >= 3:
            get_logger().info(f"连续 {consecutive_noops} 轮无改动，代码已稳定，退出。")
            cprint(f"\n✓ 连续 {consecutive_noops} 轮无改动，代码已稳定，退出。", C.GREEN)
            break

        cprint(f"\n{'─' * 55}", C.CYAN)
        cprint(f"  第 {round_num} 轮迭代{' (无限)' if max_rounds == 0 else f' / {max_rounds}'}", C.BOLD + C.CYAN)
        cprint(f"{'─' * 55}", C.CYAN)

        # 构建每轮通用 prompt
        parts = [TASK_PROMPT]
        round_info = f"\n\n## 当前迭代上下文\n\n这是第 {round_num} 轮"
        if max_rounds > 0:
            round_info += f" / 共 {max_rounds} 轮"
        round_info += "。扫描代码库，找出优先级最高的一个改进点并实现它。"
        parts.append(round_info)
        if prev_summary:
            parts.append(
                f"\n上一轮 AI 完成的改动: {prev_summary}\n"
                f"请不要再做相同的改动，继续寻找新的改进点。"
            )
        prompt = "\n".join(parts)

        # ─── 预览模式：只打印计划，不实际执行 ───
        if dry_run:
            _print_dry_run_round(agent, round_num, prompt, agent_args, ext_filter, target_dir)
            consecutive_noops = 0
            continue

        result = _execute_single_round(
            target_dir, agent, round_num, prompt, allowed_ext,
            no_backup, no_git, timeout, agent_args, ext_filter,
            keep_backups,
            error_label="等待后继续...",
        )

        if not result["success"] and not result["has_diff"]:
            consecutive_noops += 1
            prev_summary = ""
            time.sleep(sleep_between)
            continue

        if result["has_diff"]:
            prev_summary = result["summary"]
            consecutive_noops = 0
        else:
            consecutive_noops += 1
            prev_summary = ""

        cprint(f"  ⏳ 等待 {sleep_between}s...", C.CYAN)
        time.sleep(sleep_between)


# ─── 预览模式辅助函数 ──────────────────────────────────────────────────

def _build_dry_run_command(agent: str, prompt: str, agent_args: Optional[list],
                           ext_filter: Optional[str], target_dir: str) -> str:
    """构建预览模式下展示的等价命令行，供用户参考。"""
    cfg = AGENTS[agent]
    cmd_parts = [cfg["cmd"]]
    if agent_args:
        cmd_parts.extend(agent_args)
    cmd_parts.extend(cfg["args"])

    if cfg["cwd_option"]:
        cmd_parts.extend([cfg["cwd_option"], target_dir])

    full_prompt = prompt
    if ext_filter:
        full_prompt = ext_filter + "\n\n" + prompt
    cmd_parts.append(shlex.quote(full_prompt))

    return " ".join(cmd_parts)


def _print_dry_run_round(agent: str, round_num: int, prompt: str,
                         agent_args: Optional[list], ext_filter: Optional[str],
                         target_dir: str):
    """预览模式：打印单轮的详细执行计划，不实际调用 Agent。"""
    cprint(f"\n  ╔{'═' * 51}╗", C.YELLOW)
    cprint(f"  ║  预览轮次 #{round_num} — 以下为计划执行内容，不会实际修改文件 ║", C.YELLOW)
    cprint(f"  ╚{'═' * 51}╝", C.YELLOW)

    cprint(f"  📋 本轮任务提示词:", C.BOLD)
    # 打印 prompt 的前面部分（截断过长内容）
    prompt_preview = prompt[:500]
    for line in prompt_preview.split("\n"):
        cprint(f"     {line}", C.CYAN)
    if len(prompt) > 500:
        cprint(f"     ...（共 {len(prompt)} 字符，已截断显示）", C.CYAN)

    cprint(f"\n  🔧 计划执行的等价命令:", C.BOLD)
    cmd = _build_dry_run_command(agent, prompt, agent_args, ext_filter, target_dir)
    cprint(f"     {cmd}", C.GREEN)

    cprint(f"\n  ⚡ 实际操作: 跳过 Agent 调用、备份、Git 提交", C.YELLOW)


def _dry_run_task_loop(target_dir: str, agent: str, tasks: list,
                       max_rounds: int, agent_args: Optional[list]):
    """预览模式：遍历任务列表，打印每个待执行任务的详细计划。"""
    pending = [t for t in tasks if t.get("status") != "done"]
    if not pending:
        get_logger().info("预览模式: 所有任务已完成，无待执行任务。")
        return

    cprint(f"\n{'─' * 55}", C.CYAN)
    cprint(f"  预览模式: 以下 {len(pending)} 个任务将按顺序执行（不会实际修改文件）", C.YELLOW + C.BOLD)
    cprint(f"{'─' * 55}", C.CYAN)

    round_num = 0
    for task in pending:
        round_num += 1
        if max_rounds > 0 and round_num > max_rounds:
            remaining = len(pending) - round_num + 1
            get_logger().info(f"预览模式: 达到最大轮次 {max_rounds}（剩余 {remaining} 个任务不会执行）")
            break

        tid = task.get("id", "?")
        title = task.get("title", "")
        desc = task.get("description", "")
        ttype = task.get("type", "")
        prio = task.get("priority", "?")

        cprint(f"\n{'─' * 55}", C.CYAN)
        cprint(f"  任务 #{tid} [{prio}] [{ttype}] {title}", C.BOLD + C.CYAN)
        cprint(f"{'─' * 55}", C.CYAN)

        cprint(f"  描述: {desc}", C.CYAN)

        # 构建任务 prompt 并打印计划
        prompt = build_task_prompt(task)
        _print_dry_run_round(agent, round_num, prompt, agent_args, None, target_dir)

    cprint(f"\n{'─' * 55}", C.CYAN)
    cprint(f"  预览完成: 共 {len(pending)} 个待执行任务，预览 {min(round_num, len(pending))} 个", C.GREEN + C.BOLD)
    cprint(f"  运行不带 --dry-run 的命令可正式执行", C.CYAN)
    cprint(f"{'─' * 55}", C.CYAN)


# ─── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI 自迭代控制器 — 调用外部 Agent 持续改进代码",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            示例:
              python ai_controller.py ./my-project --agent pi --max-rounds 10    # 默认：先规划再执行
              python ai_controller.py ./my-project --agent pi --plan-only        # 仅生成任务列表
              python ai_controller.py ./my-project --agent pi --no-plan          # 传统逐轮模式
              python ai_controller.py ./my-project --agent pi --resume           # 恢复迭代
        """),
    )
    parser.add_argument("directory", help="目标代码目录")
    parser.add_argument("--agent", choices=list(AGENTS.keys()), default="pi",
                        help="使用的 Agent 工具 (默认 pi)")
    parser.add_argument("--max-rounds", type=int, default=10,
                        help="最大迭代轮数，0=无限 (默认 10)")
    parser.add_argument("--ext", default="",
                        help="只处理指定后缀，逗号分隔，如 .py,.ts,.js")
    parser.add_argument("--timeout", type=int, default=600,
                        help="Agent 单轮超时秒数 (默认 600)")
    parser.add_argument("--sleep", type=float, default=2.0,
                        help="每轮间隔秒数 (默认 2.0)")
    parser.add_argument("--no-backup", action="store_true",
                        help="不备份（危险！）")
    parser.add_argument("--no-git", action="store_true",
                        help="不自动 git commit")
    parser.add_argument("--agent-args", default="",
                        help="传递给 Agent 的额外参数，用引号包裹，如 --agent-args '--model gpt-4'")
    parser.add_argument("--resume", action="store_true",
                        help="从中断处恢复：读取 changelog 找到上次进度，从下一轮继续迭代")
    parser.add_argument("--keep-backups", type=int, default=0,
                        help="只保留最近 N 个备份，旧备份自动清理（0=不限制，默认 0）")
    parser.add_argument("--no-plan", action="store_true",
                        help="跳过任务列表规划阶段，使用传统逐轮模式（每轮 AI 自行选择改进点）")
    parser.add_argument("--plan-only", action="store_true",
                        help="只生成任务列表 AI-TASKS.md，不执行")
    parser.add_argument("--dry-run", action="store_true",
                        help="预览模式：跳过 Agent 调用和文件修改，只打印每轮要执行的任务描述和命令")

    args = parser.parse_args()

    target = Path(args.directory).resolve()
    if not target.is_dir():
        get_logger().error(f"目录不存在: {args.directory}")
        sys.exit(1)

    # 校验数值参数，在进入主循环前拦截非法输入，避免运行时出现难以理解的错误
    if args.max_rounds < 0:
        get_logger().error(f"--max-rounds 不能为负数（0=无限），当前值: {args.max_rounds}")
        sys.exit(1)
    if args.timeout <= 0:
        get_logger().error(f"--timeout 必须为正数，当前值: {args.timeout}")
        sys.exit(1)
    if args.sleep < 0:
        get_logger().error(f"--sleep 不能为负数，当前值: {args.sleep}")
        sys.exit(1)
    if args.keep_backups < 0:
        get_logger().error(f"--keep-backups 不能为负数（0=不限制），当前值: {args.keep_backups}")
        sys.exit(1)

    # 检查 agent 是否可用
    agent_cmd = AGENTS[args.agent]["cmd"]
    if shutil.which(agent_cmd) is None:
        get_logger().error(f"找不到 {agent_cmd} 命令，请确认 {args.agent} 已安装")
        sys.exit(1)

    # 解析 agent 额外参数
    agent_args = None
    if args.agent_args:
        agent_args = shlex.split(args.agent_args)

    # 解析后缀
    allowed_ext = None
    if args.ext:
        allowed_ext = set()
        for e in args.ext.split(","):
            e = e.strip()
            if e and not e.startswith("."):
                e = "." + e
            if e:
                allowed_ext.add(e)

    # --plan-only：只生成任务列表
    if args.plan_only:
        print()
        cprint("╔══════════════════════════════════════════╗", C.CYAN)
        cprint("║      AI 自迭代控制器 v2.1 (仅规划)       ║", C.CYAN)
        cprint("╚══════════════════════════════════════════╝", C.CYAN)
        print()
        ext_filter = build_ext_filter_arg(args.agent, allowed_ext)
        tasks = generate_task_list(args.agent, str(target), ext_filter, args.timeout, agent_args)
        if tasks is None:
            get_logger().error("规划失败，未能生成任务列表。")
            sys.exit(1)
        if len(tasks) == 0:
            get_logger().info("AI 评估后认为代码库已完善，无需改进。")
        else:
            save_task_list(str(target), tasks)
            get_logger().info(f"任务列表已保存至 {TASK_FILE}（共 {len(tasks)} 个任务）")
        sys.exit(0)

    try:
        run_loop(
            target_dir=str(target),
            agent=args.agent,
            max_rounds=args.max_rounds,
            allowed_ext=allowed_ext,
            no_backup=args.no_backup,
            no_git=args.no_git,
            sleep_between=args.sleep,
            timeout=args.timeout,
            agent_args=agent_args,
            resume=args.resume,
            keep_backups=args.keep_backups,
            no_plan=args.no_plan,
            dry_run=args.dry_run,
        )
    except KeyboardInterrupt:
        cprint("\n\n⏹ 用户中断，退出。", C.YELLOW)
        get_logger().warning("用户中断，退出。")


if __name__ == "__main__":
    main()
