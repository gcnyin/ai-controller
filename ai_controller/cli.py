"""CLI 入口与主循环 -- 命令行解析、迭代调度、日志记录。"""

import sys
import time
import shlex
import shutil
import argparse
import importlib.metadata
import textwrap
from pathlib import Path
from datetime import datetime
from typing import Optional


import logging

from . import LOG_FILE, LOGGER_FILE
from .config import load_config
from .agent import AGENTS, call_agent, build_agent_command, run_test_command
from .prompts import build_task_prompt, build_retry_prompt
from .tasks import (
    TASK_FILE,
    generate_task_list,
    save_task_list,
    load_task_list,
    load_task_metadata,
    mark_task_done,
    get_next_pending_task,
    backup_task_file,
)
from .backup import BACKUP_DIR_NAME, backup_all, cleanup_old_backups
from .git_ops import (
    is_git_repo,
    has_changes,
    git_commit,
    git_stash_push,
    git_stash_pop,
    get_changed_files,
    get_git_diff_summary,
)



# ─── 日志记录辅助 ──────────────────────────────────────────────────────

def extract_model_hint(agent_args: Optional[list]) -> str:
    """从 agent 额外参数中提取模型信息,用于 changelog 头部显示。

    支持 --model <name> 和 -m <name> 两种写法。
    如果同时出现多个,取最后一个。
    """
    if not agent_args:
        return ""
    # 迭代查找 --model / -m,取最后出现的值
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


def _parse_task_ids(raw: str) -> set[int]:
    """解析 --task-ids 字符串，支持逗号分隔和范围格式（如 1,3,5 或 1-3,5）。

    Returns:
        解析后的整数集合，解析失败返回空集合。
    """
    if not raw or not raw.strip():
        return set()
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                start_s, end_s = part.split("-", 1)
                start, end = int(start_s), int(end_s)
                if start > end:
                    return set()
                ids.update(range(start, end + 1))
            except (ValueError, TypeError):
                return set()
        else:
            try:
                ids.add(int(part))
            except ValueError:
                return set()
    return ids


def _filter_tasks_by_ids(tasks: list, task_ids: set) -> list:
    """按 task_ids 过滤任务列表，保留匹配 ID 的任务。

    已完成且匹配的任务保留其完成状态；不匹配 ID 的任务不包含在返回结果中。
    对 task_ids 中存在但 tasks 中不存在的 ID，记录警告。

    过滤仅作用于内存中的可执行子集，不会覆盖磁盘上的完整任务列表。
    """
    task_id_set = {t.get("id") for t in tasks}
    missing = task_ids - task_id_set
    if missing:
        logger.warning(
            "--task-ids 中 %d 个 ID 在任务列表中不存在: %s",
            len(missing), ",".join(str(i) for i in sorted(missing)),
        )
    filtered = [t for t in tasks if t.get("id") in task_ids]
    logger.info(
        "按 --task-ids 过滤: 保留 %d/%d 个任务",
        len(filtered), len(tasks),
    )
    return filtered


def _setup_logging(target_dir: str):
    """配置标准 logging:控制台 + 文件双输出。"""
    root_logger = logging.getLogger("ai_controller")
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()

    # 控制台 handler - INFO 及以上
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    root_logger.addHandler(ch)

    # 文件 handler - DEBUG 及以上
    log_path = Path(target_dir) / LOGGER_FILE
    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root_logger.addHandler(fh)


logger = logging.getLogger(__name__)


# ─── .gitignore 管理 ────────────────────────────────────────────────

def ensure_gitignore(target_dir: str) -> bool:
    """确保目标目录的 .gitignore 包含所有 AI 控制器生成的文件/目录路径。

    检查目标目录下的 .gitignore 文件，如果缺少 AI-TASKS.md、
    AI-CHANGELOG.md、ai-controller.log、.ai-controller-backups/ 等路径，
    自动追加一个带注释标题的段落。

    Returns:
        True 表示 .gitignore 已被修改，False 表示无需修改。
    """
    generated_entries = [
        TASK_FILE,               # AI-TASKS.md
        LOG_FILE,                # AI-CHANGELOG.md
        LOGGER_FILE,             # ai-controller.log
        BACKUP_DIR_NAME + "/",   # .ai-controller-backups/
    ]

    gitignore_path = Path(target_dir) / ".gitignore"
    if not gitignore_path.is_file():
        if not is_git_repo(target_dir):
            return False
        with open(gitignore_path, "w", encoding="utf-8") as f:
            f.write("# AI 自迭代控制器 生成文件\n")
            for entry in generated_entries:
                f.write(entry + "\n")
        logger.info("已创建 .gitignore 并添加 %d 个控制器条目", len(generated_entries))
        return True

    content = gitignore_path.read_text(encoding="utf-8", errors="replace")
    existing_lines = set(line.strip() for line in content.splitlines())

    missing = [p for p in generated_entries if p not in existing_lines]
    if not missing:
        return False

    # 追加缺失条目
    with open(gitignore_path, "a", encoding="utf-8") as f:
        f.write("\n# AI 自迭代控制器 生成文件\n")
        for entry in missing:
            f.write(entry + "\n")

    logger.info(
        "已将 %d 个路径自动追加到 .gitignore: %s",
        len(missing), ", ".join(missing),
    )
    return True


def init_log(target_dir: str, agent: str, model_hint: str = ""):
    """初始化 changelog 文件和日志系统。

    如果 AI-CHANGELOG.md 已存在则追加模式(不覆盖),
    同时初始化 logging 使其同时输出到控制台和 ai-controller.log 文件。
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

    # 设置双输出 logger(控制台 + 文件)
    _setup_logging(target_dir)
    logger.info(f"AI 自迭代控制器启动 - Agent: {agent}{model_str}, 目标: {target_dir}")


def write_run_header(target_dir: str, run_count: int):
    """在 changelog 中写入本次运行头部。

    每次程序调用写入一行运行头部,方便追踪跨运行进度。
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"## 运行 #{run_count} - {ts}",
        "",
    ]

    log_path = Path(target_dir) / LOG_FILE
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    logger.info(f"运行 #{run_count} 开始")


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
        f"## Round {round_num} - {ts}",
        "",
        f"改动说明: {summary}",
        "",
    ]
    if changed_files:
        lines.append(f"改动文件 ({len(changed_files)} 个):")
        for f in changed_files:
            lines.append(f"- `{f}`")
    else:
        lines.append("改动文件: 无(本轮无代码变更)")

    lines.append("")
    lines.append(f"*耗时 {elapsed:.1f}s*")
    lines.append("")

    log_path = Path(target_dir) / LOG_FILE
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")




# ─── 单轮执行 ─────────────────────────────────────────────────────────

def _execute_single_round(
    target_dir: str,
    agent: str,
    round_num: int,
    prompt: str,
    no_backup: bool,
    timeout: int,
    agent_args: Optional[list],
    keep_backups: int,
    summary_prefix: str = "",
    error_label: str = "Agent 返回异常",
    defer_commit: bool = False,
    no_commit: bool = False,
) -> dict:
    """执行单轮迭代的核心逻辑：备份、调用 Agent、检测改动、记录日志、Git 提交。

    Args:
        summary_prefix: 写入 changelog 时加在 summary 前面的前缀（如 "[任务#1] "）
        error_label: Agent 异常且无改动时的日志描述

    Returns:
        dict with keys:
            success: Agent 是否正常退出
            summary: 本轮改动说明
            changed_files: 改动的文件列表
            elapsed: Agent 执行耗时（秒）
            has_diff: 是否有文件改动（Agent 异常且无改动时为 False）
    """
    git_repo = is_git_repo(target_dir)

    # ── 备份 ──（git 仓库已有版本历史，无需全量备份）
    if not no_backup and not git_repo:
        backup_folder = backup_all(target_dir, round_num)
        if backup_folder:
            print(f"  \U0001f4be 已备份到: {backup_folder}")
        if keep_backups > 0:
            cleanup_old_backups(target_dir, keep_backups)
    before_ts = time.time()

    # ── 调用 Agent ──
    success, summary, raw_output, elapsed = call_agent(
        agent, prompt, target_dir, timeout, agent_args,
        quiet=True,
    )

    print()

    # ── 检测文件改动 ──
    changed_files = get_changed_files(target_dir, before_ts)
    has_diff = bool(changed_files)

    # ── Agent 异常且无改动:记录日志后返回 ──
    if not success and not has_diff:
        logger.warning(f"Agent 返回异常,{error_label}")
        write_round_log(target_dir, round_num, f"{summary_prefix}{summary}", [], elapsed)
        return {"success": False, "summary": summary, "changed_files": [], "elapsed": elapsed, "has_diff": False}

    # ── Agent 异常但有改动:警告后继续处理 ──
    if not success and has_diff:
        logger.warning("Agent 返回异常但仍有文件改动,继续处理...")

    if has_diff:
        write_round_log(target_dir, round_num, f"{summary_prefix}{summary}", changed_files, elapsed)

        # ── Git 提交 ──        
        if git_repo and not defer_commit and not no_commit:
            diff_stat = get_git_diff_summary(target_dir)
            git_commit(target_dir, round_num, summary)
            if diff_stat:
                print(f"  \u2713 改动: {diff_stat}")
            else:
                print(f"  \u2713 已提交改动")
        else:
            print(f"  \u2713 修改了 {len(changed_files)} 个文件")

        print(f"  \U0001f4c4 {', '.join(changed_files[:5])}"
               f"{' ...' if len(changed_files) > 5 else ''}")
    else:
        logger.info(f"本轮无文件改动 - {summary}")
        write_round_log(target_dir, round_num, f"{summary_prefix}{summary}", [], elapsed)

    return {"success": success, "summary": summary, "changed_files": changed_files, "elapsed": elapsed, "has_diff": has_diff}


# ─── 带测试重试的任务执行 ─────────────────────────────────────────────

def _execute_task_with_retry(
    target_dir: str,
    agent: str,
    task: dict,
    task_prompt: str,
    test_command: Optional[str],
    max_retries: int,
    no_backup: bool,
    timeout: int,
    agent_args: Optional[list],
    keep_backups: int,
    round_num: int,
    sleep_between: float,
    no_commit: bool = False,
) -> dict:
    """执行单个任务，带测试验证和重试。

    流程：
    1. 调用 Agent 执行任务
    2. 如果有文件改动且有 test_command，运行测试
    3. 测试失败则构建修复 prompt，重试（最多 max_retries 次）
    4. 全部重试用尽仍失败则放弃

    Returns:
        dict with keys:
            success: 最终是否通过测试（或无测试命令时等于 agent 是否成功）
            summary: 最终轮次的改动说明
            changed_files: 最终轮次的改动文件列表
            elapsed: 累计耗时（秒）
            has_diff: 是否有文件改动
            retries_used: 实际使用的重试次数
            final_test_passed: 最后的测试是否通过（无测试命令时为 None）
    """
    task_id = task.get("id", "?")
    task_title = task.get("title", "")
    retries_used = 0
    total_elapsed = 0.0
    current_prompt = task_prompt
    final_test_passed = None
    summary_prefix = f"[任务#{task_id}] "

    for attempt in range(max_retries + 1):
        is_retry = attempt > 0
        if is_retry:
            print(f"\n  🔄 测试失败，开始第 {attempt}/{max_retries} 次重试...")

        # 调用 Agent 执行（或修复）
        result = _execute_single_round(
            target_dir, agent, round_num, current_prompt,
            no_backup, timeout, agent_args, keep_backups,
            summary_prefix=summary_prefix,
            error_label=f"任务 #{task_id} 执行失败",
            defer_commit=True,  # 始终推迟提交，由外层统一处理
            no_commit=no_commit,
        )
        total_elapsed += result["elapsed"]

        # Agent 失败且无改动：直接返回
        if not result["success"] and not result["has_diff"]:
            result["elapsed"] = total_elapsed
            result["retries_used"] = retries_used
            result["final_test_passed"] = final_test_passed
            return result

        # 没有测试命令：直接认为通过
        if not test_command:
            result["elapsed"] = total_elapsed
            result["retries_used"] = retries_used
            result["final_test_passed"] = None
            return result

        # 首次尝试无文件改动时跳过测试；重试阶段始终运行测试
        if not is_retry and not result["has_diff"]:
            result["elapsed"] = total_elapsed
            result["retries_used"] = retries_used
            result["final_test_passed"] = None
            return result

        # 运行测试
        test_passed, test_output = run_test_command(
            test_command, target_dir, timeout,
        )
        final_test_passed = test_passed

        if test_passed:
            result["elapsed"] = total_elapsed
            result["retries_used"] = retries_used
            result["final_test_passed"] = True
            return result

        # 测试失败
        if attempt < max_retries:
            retries_used += 1
            # 还有重试机会，构建修复 prompt
            current_prompt = build_retry_prompt(
                task,
                test_command,
                test_output,
                result["changed_files"],
            )
            time.sleep(sleep_between)
        # attempt >= max_retries 时自然退出循环

    # 所有重试用尽
    logger.warning(
        f"任务 #{task_id} - {task_title}: "
        f"测试失败，已用完 {max_retries} 次重试，放弃此任务"
    )
    result["elapsed"] = total_elapsed
    result["retries_used"] = retries_used
    result["final_test_passed"] = False
    # 标记为失败但仍返回 has_diff，让外层记录
    return result


# ─── 主循环 ────────────────────────────────────────────────────────────

def run_loop(
    target_dir: str,
    agent: str,
    max_rounds: int = 10,
    max_retries: int = 3,
    no_backup: bool = False,
    sleep_between: float = 2.0,
    timeout: int = 600,
    agent_args: Optional[list] = None,
    keep_backups: int = 0,
    replan: bool = False,
    dry_run: bool = False,
    no_commit: bool = False,
    test_command_override: Optional[str] = None,
    task_ids: Optional[set] = None,
):
    print()
    print("╔═══════════════════════════════════════════════╗")
    print("║           AI 自迭代控制器 v3.0               ║")
    print("╚═══════════════════════════════════════════════╝")
    print()
    print(f"  目标目录 : {target_dir}")
    print(f"  Agent    : {agent}")
    print(f"  超时     : {timeout}s")
    print(f"  最大轮次 : {'无限' if max_rounds == 0 else max_rounds}")

    git_repo_for_display = is_git_repo(target_dir)
    if not no_backup and not git_repo_for_display:
        print(f"  备份目录 : {BACKUP_DIR_NAME}/")
        if keep_backups > 0:
            print(f"  备份保留 : 最近 {keep_backups} 个")
    elif not no_backup and git_repo_for_display:
        print(f"  备份     : 跳过(Git 仓库已有版本历史)")
    if is_git_repo(target_dir):
        if no_commit:
            print(f"  Git      : 自动 commit (已禁用)")
        else:
            print(f"  Git      : 自动 commit")




    if dry_run:
        print(f"  模式     : 预览模式(不实际修改任何文件)")
    print()

    # 自动管理 .gitignore：将生成的文件路径追加到目标仓库的忽略列表
    ensure_gitignore(target_dir)

    model_hint = extract_model_hint(agent_args)
    init_log(target_dir, agent, model_hint)
    if model_hint:
        print(f"  模型     : {model_hint}")

    # 检查工作区是否有未提交的改动,如有则自动 stash 隔离
    stashed = False
    if is_git_repo(target_dir) and has_changes(target_dir):
        logger.info("工作区存在未提交的改动,自动 stash 隔离...")
        stashed = git_stash_push(target_dir)
        if stashed:
            logger.info("已自动 stash 用户改动,执行完成后将自动恢复")
        else:
            logger.warning("自动 stash 失败,用户改动可能与 AI 改动混合记录")

    # ─── 任务列表模式：自动恢复 + 可选重新规划 ───

    tasks = None
    metadata = {}
    task_file = Path(target_dir) / TASK_FILE
    has_existing_tasks = task_file.is_file()

    if has_existing_tasks and replan:
        # --replan:备份旧文件,强制重新生成
        backup_task_file(target_dir)
        has_existing_tasks = False
        logger.info("重新规划模式: 已备份旧任务列表,将生成全新任务列表")

    if has_existing_tasks:
        # 自动恢复:加载已有任务列表和元信息
        tasks = load_task_list(target_dir)
        metadata = load_task_metadata(target_dir)

        if tasks:
            pending = [t for t in tasks if t.get("status") != "done"]
            done_count = len(tasks) - len(pending)
            logger.info(
                f"自动恢复: 从 {TASK_FILE} 加载 "
                f"(已完成 {done_count}/{len(tasks)} 个任务,"
                f"已运行 {metadata.get('run_count', 1)} 次)"
            )
            if not pending:
                logger.info("任务列表中所有任务已完成,退出。")
                if stashed:
                    git_stash_pop(target_dir)
                return
        else:
            logger.warning("无法加载任务列表,将重新生成。")
            tasks = None

    test_command = metadata.get("test_command", "") or None

    if tasks is None:
        # 全新生成任务列表
        if dry_run and task_file.is_file():
            # 预览模式:如果已存在任务列表文件则直接加载,避免重新调用 Agent
            tasks = load_task_list(target_dir)
            metadata = load_task_metadata(target_dir)
            test_command = metadata.get("test_command", "") or None
            logger.info("预览模式: 加载已有任务列表,跳过规划阶段 Agent 调用")
        if tasks is None:
            tasks, test_command = generate_task_list(agent, target_dir, timeout, agent_args)
        if tasks is None:
            logger.error("任务列表生成失败，退出。")
            if stashed:
                git_stash_pop(target_dir)
            return

        if len(tasks) == 0:
            logger.info("AI 评估后认为代码库已完善,无需改进")
            if stashed:
                git_stash_pop(target_dir)
            return

        # 全新生成:初始化元信息
        metadata = {"run_count": 1, "last_run": "", "global_round": 0}

        save_task_list(target_dir, tasks,
                       run_count=metadata["run_count"],
                       last_run=metadata["last_run"],
                       global_round=metadata["global_round"],
                       test_command=test_command)
        logger.info(f"任务列表已生成: {len(tasks)} 个任务,保存至 {TASK_FILE}")

        # 打印全部任务概览
        logger.info(f"{'─' * 40}")
        logger.info(f"任务列表(共 {len(tasks)} 个):")
        for t in tasks:
            logger.info(f"  #{t.get('id')} [{t.get('priority', '?')}] [{t.get('type', '')}] {t.get('title', '')}")
        logger.info(f"{'─' * 40}")

    # ─── 按 --task-ids 过滤任务 ───
    active_tasks = tasks
    if task_ids is not None and tasks is not None:
        active_tasks = _filter_tasks_by_ids(tasks, task_ids)
        if not active_tasks:
            logger.info("所有指定 ID 的任务已完成或不存在，退出。")
            if stashed:
                git_stash_pop(target_dir)
            return

    # ─── 测试命令覆盖（命令行参数优先于 AI 生成/文件加载）───
    if test_command_override is not None:
        test_command = test_command_override if test_command_override.strip() else None
        if test_command:
            logger.info(f"测试命令(手动指定): {test_command}")
            # 回写到元信息中，确保后续 save_task_list 输出正确的测试命令
            metadata["test_command"] = test_command

    # ─── 更新运行元信息 ───
    run_count = metadata.get("run_count", 1) + 1 if has_existing_tasks else metadata.get("run_count", 1)
    last_run = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    global_round = metadata.get("global_round", 0)
    gen_time = metadata.get("gen_time", "")  # 恢复模式保留原始生成时间
    write_run_header(target_dir, run_count)

    # ─── 显示测试命令 ───
    if test_command:
        logger.info(f"测试命令: {test_command}")

    # ─── 预览模式:打印任务执行计划后退出 ───
    if dry_run:
        _dry_run_task_loop(target_dir, agent, active_tasks, max_rounds, agent_args, test_command)
        if stashed:
            git_stash_pop(target_dir)
        return

    # ─── 阶段 2:逐条执行任务 ───
    round_num = global_round
    consecutive_noops = 0
    current_task_id = None
    git_repo = is_git_repo(target_dir)

    try:
        while True:
            # 获取下一个待执行任务(从内存缓存查找,避免重复解析文件)
            task = get_next_pending_task(target_dir, active_tasks)

            # 任务切换时重置连续无改动计数器(每个任务独立计数)
            if task is not None and task.get("id") != current_task_id:
                current_task_id = task["id"]
                consecutive_noops = 0
            if task is None:
                logger.info("所有任务已完成!")
                break

            round_num += 1

            if max_rounds > 0 and round_num > max_rounds:
                pending_left = sum(1 for t in active_tasks if t.get("status") != "done")
                logger.info(
                    f"达到最大轮次 {max_rounds}(剩余 {pending_left} 个待执行任务),退出。"
                )
                break

            # 连续无改动:跳过当前任务,标记完成,继续下一个
            if consecutive_noops >= 3:
                tid = task.get("id", "?")
                title = task.get("title", "")
                logger.info(
                    f"连续 {consecutive_noops} 轮无改动,跳过任务 #{tid} - {title}"
                )
                mark_task_done(target_dir, task["id"], round_num, tasks,
                               run_count=run_count, last_run=last_run,
                               global_round=round_num, gen_time=gen_time)
                if git_repo and not no_commit:
                    git_commit(target_dir, round_num, f"Skip task #{tid}: {title}")
                consecutive_noops = 0
                time.sleep(sleep_between)
                continue

            tid = task.get("id", "?")
            title = task.get("title", "")

            print(f"\n{'─' * 55}")
            print(f"  第 {round_num} 轮: 执行任务 #{tid} - {title}")
            print(f"{'─' * 55}")

            # 构建任务 prompt 并使用带测试重试的执行
            prompt = build_task_prompt(task)
            result = _execute_task_with_retry(
                target_dir, agent, task, prompt, test_command,
                max_retries, no_backup, timeout, agent_args, keep_backups,
                round_num, sleep_between,
                no_commit=no_commit,
            )

            if not result["success"] and not result["has_diff"]:
                # Agent 失败且无改动
                consecutive_noops += 1
                time.sleep(sleep_between)
                continue

            # 记录重试信息
            retries_used = result.get("retries_used", 0)
            if retries_used > 0:
                if result.get("final_test_passed"):
                    logger.info(
                        f"任务 #{tid} 经过 {retries_used} 次重试后测试通过"
                    )
                else:
                    logger.warning(
                        f"任务 #{tid} 测试失败，已用完 {retries_used} 次重试"
                    )

            mark_task_done(target_dir, task["id"], round_num, tasks,
                           run_count=run_count, last_run=last_run,
                           global_round=round_num, gen_time=gen_time)
            consecutive_noops = 0

            if git_repo and not no_commit:
                git_commit(target_dir, round_num, result["summary"])

            print(f"  ⏳ 等待 {sleep_between}s...")
            time.sleep(sleep_between)

    except KeyboardInterrupt:
        print("\n\n⏹ 用户中断，保存当前进度后退出。")
        logger.warning("用户中断，保存当前进度后退出。")

    # ─── 退出前保存最终状态 ───
    save_task_list(target_dir, tasks,
                   run_count=run_count, last_run=last_run,
                   global_round=round_num, gen_time=gen_time,
                   test_command=test_command)

    if stashed:
        git_stash_pop(target_dir)


# ─── 预览模式辅助函数 ──────────────────────────────────────────────────

def _build_dry_run_command(agent: str, prompt: str, agent_args: Optional[list],
                           target_dir: str) -> str:
    """构建预览模式下展示的等价命令行,供用户参考。

    复用 build_agent_command 构造命令列表,仅将最后一个参数(prompt)
    用 shlex.quote 包裹后拼接为可复制的字符串。
    """
    cmd_parts, _ = build_agent_command(
        agent, prompt, target_dir, agent_args,
    )
    # prompt 作为最后一个参数,quote 以安全展示
    cmd_parts[-1] = shlex.quote(cmd_parts[-1])
    return " ".join(cmd_parts)


def _print_dry_run_round(agent: str, round_num: int, prompt: str,
                         agent_args: Optional[list],
                         target_dir: str):
    """预览模式:打印单轮的详细执行计划,不实际调用 Agent。"""
    print(f"\n  ╔{'═' * 51}╗")
    print(f"  ║  预览轮次 #{round_num} - 以下为计划执行内容,不会实际修改文件 ║")
    print(f"  ╚{'═' * 51}╝")

    print(f"  📋 本轮任务提示词:")
    # 打印 prompt 的前面部分(截断过长内容)
    prompt_preview = prompt[:500]
    for line in prompt_preview.split("\n"):
        print(f"     {line}")
    if len(prompt) > 500:
        print(f"     ...(共 {len(prompt)} 字符,已截断显示)")

    print(f"\n  🔧 计划执行的等价命令:")
    cmd = _build_dry_run_command(agent, prompt, agent_args, target_dir)
    print(f"     {cmd}")

    print(f"\n  ⚡ 实际操作: 跳过 Agent 调用、备份、Git 提交")


def _dry_run_task_loop(target_dir: str, agent: str, tasks: list,
                       max_rounds: int, agent_args: Optional[list],
                       test_command: Optional[str] = None):
    """预览模式:遍历任务列表,打印每个待执行任务的详细计划。"""
    pending = [t for t in tasks if t.get("status") != "done"]
    if not pending:
        logger.info("预览模式: 所有任务已完成,无待执行任务。")
        return

    print(f"\n{'─' * 55}")
    print(f"  预览模式: 以下 {len(pending)} 个任务将按顺序执行(不会实际修改文件)")
    print(f"{'─' * 55}")

    if test_command:
        print(f"\n  🧪 测试命令: {test_command}")
        print(f"     每轮执行后运行此命令验证，失败则自动重试（最多 --max-retries 次）")

    round_num = 0
    for task in pending:
        round_num += 1
        if max_rounds > 0 and round_num > max_rounds:
            remaining = len(pending) - round_num + 1
            logger.info(f"预览模式: 达到最大轮次 {max_rounds}(剩余 {remaining} 个任务不会执行)")
            break

        tid = task.get("id", "?")
        title = task.get("title", "")
        desc = task.get("description", "")
        ttype = task.get("type", "")
        prio = task.get("priority", "?")

        print(f"\n{'─' * 55}")
        print(f"  任务 #{tid} [{prio}] [{ttype}] {title}")
        print(f"{'─' * 55}")

        print(f"  描述: {desc}")

        # 构建任务 prompt 并打印计划
        prompt = build_task_prompt(task)
        _print_dry_run_round(agent, round_num, prompt, agent_args, target_dir)

    print(f"\n{'─' * 55}")
    print(f"  预览完成: 共 {len(pending)} 个待执行任务,预览 {min(round_num, len(pending))} 个")
    print(f"  运行不带 --dry-run 的命令可正式执行")
    print(f"{'─' * 55}")


# ─── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI 自迭代控制器 - 调用外部 Agent 持续改进代码",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            示例:
              python -m ai_controller ./my-project --agent pi --max-rounds 10    # 先规划再执行,自动恢复
              python -m ai_controller ./my-project --agent pi --replan           # 重新生成任务列表
              python -m ai_controller ./my-project --agent pi --plan-only        # 仅生成任务列表
        """),
    )
    parser.add_argument("directory", help="目标代码目录")
    parser.add_argument("--agent", choices=list(AGENTS.keys()), default="pi",
                        help="使用的 Agent 工具 (默认 pi)")
    parser.add_argument("--max-rounds", type=int, default=10,
                        help="最大迭代轮数,0=无限 (默认 10)")
    parser.add_argument("--max-retries", type=int, default=3,
                        help="测试失败时最大重试次数 (默认 3)")

    parser.add_argument("--timeout", type=int, default=600,
                        help="Agent 单轮超时秒数 (默认 600)")
    parser.add_argument("--sleep", type=float, default=2.0,
                        help="每轮间隔秒数 (默认 2.0)")
    parser.add_argument("--no-backup", action="store_true",
                        help="强制不备份(危险!若为 git 仓库则自动跳过备份)")
    parser.add_argument("--agent-args", default="",
                        help="传递给 Agent 的额外参数,用引号包裹,如 --agent-args '--model gpt-4'")
    parser.add_argument("--keep-backups", type=int, default=0,
                        help="只保留最近 N 个备份,旧备份自动清理(0=不限制,默认 0)")

    parser.add_argument("--plan-only", action="store_true",
                        help="只生成任务列表 AI-TASKS.md,不执行")
    parser.add_argument("--replan", action="store_true",
                        help="强制重新生成任务列表(备份旧 AI-TASKS.md 为 .bak)")

    parser.add_argument("--dry-run", action="store_true",
                        help="预览模式:跳过 Agent 调用和文件修改,只打印每轮要执行的任务描述和命令")
    parser.add_argument("--no-commit", action="store_true",
                        help="跳过 Git 自动提交,但仍记录 changelog")
    parser.add_argument("--test-command", default=None,
                        help="手动指定测试命令,覆盖 AI 规划阶段输出的 test_command")
    parser.add_argument("--task-ids", default="",
                        help="只运行指定 ID 的任务子集，逗号分隔（如 1,3,5 或 1-3,5）")

    try:
        _version = importlib.metadata.version("ai-controller")
    except importlib.metadata.PackageNotFoundError:
        _version = "unknown"
    parser.add_argument("-V", "--version", action="version",
                        version=f"%(prog)s {_version}")

    # ── 第一阶段:仅解析 directory 参数,用于定位配置文件 ──
    # 用 partial parse 只拿到 directory,忽略其他参数的缺失
    prelim_args, _ = parser.parse_known_args()

    # ── 加载配置文件(如果存在)──
    prelim_target = Path(prelim_args.directory).resolve()
    config = load_config(str(prelim_target))
    if config:
        log_target = str(prelim_target)
        print(f"已加载配置文件: {', '.join(f'{k}={v}' for k, v in sorted(config.items()))}")
        parser.set_defaults(**config)

    # ── 正式解析(命令行参数覆盖配置文件值)──
    args = parser.parse_args()

    target = Path(args.directory).resolve()
    if not target.is_dir():
        logger.error(f"目录不存在: {args.directory}")
        sys.exit(1)

    # 校验数值参数,在进入主循环前拦截非法输入,避免运行时出现难以理解的错误
    if args.max_rounds < 0:
        logger.error(f"--max-rounds 不能为负数(0=无限),当前值: {args.max_rounds}")
        sys.exit(1)
    if args.max_retries < 0:
        logger.error(f"--max-retries 不能为负数,当前值: {args.max_retries}")
        sys.exit(1)
    if args.timeout <= 0:
        logger.error(f"--timeout 必须为正数,当前值: {args.timeout}")
        sys.exit(1)
    if args.sleep < 0:
        logger.error(f"--sleep 不能为负数,当前值: {args.sleep}")
        sys.exit(1)
    if args.keep_backups < 0:
        logger.error(f"--keep-backups 不能为负数(0=不限制),当前值: {args.keep_backups}")
        sys.exit(1)

    # 检查 agent 是否可用
    agent_cmd = AGENTS[args.agent]["cmd"]
    if shutil.which(agent_cmd) is None:
        logger.error(f"找不到 {agent_cmd} 命令,请确认 {args.agent} 已安装")
        sys.exit(1)

    # 解析 agent 额外参数
    agent_args = None
    if args.agent_args:
        agent_args = shlex.split(args.agent_args)

    # 解析 --task-ids
    task_ids = None
    if args.task_ids:
        task_ids = _parse_task_ids(args.task_ids)
        if not task_ids:
            logger.error("--task-ids 格式错误: %s (示例: 1,3,5 或 1-3,5)", args.task_ids)
            sys.exit(1)

    # --plan-only:只生成任务列表
    if args.plan_only:
        print()
        print("╔══════════════════════════════════════════╗")
        print("║           AI 自迭代控制器 v3.0 (仅规划)     ║")
        print("╚══════════════════════════════════════════╝")
        print()

        # 如果 --replan --plan-only,备份旧文件再生成
        if args.replan:
            backup_task_file(str(target))

        tasks, test_command = generate_task_list(args.agent, str(target), args.timeout, agent_args)
        if tasks is None:
            logger.error("规划失败,未能生成任务列表。")
            sys.exit(1)

        # 命令行 --test-command 覆盖 AI 生成的 test_command
        if args.test_command is not None:
            test_command = args.test_command if args.test_command.strip() else None
            if test_command:
                logger.info(f"测试命令(手动指定): {test_command}")

        if len(tasks) == 0:
            logger.info("AI 评估后认为代码库已完善,无需改进。")
        else:
            save_task_list(str(target), tasks, run_count=1,
                           last_run="", global_round=0,
                           test_command=test_command)
            logger.info(f"任务列表已保存至 {TASK_FILE}(共 {len(tasks)} 个任务)")
            if test_command:
                logger.info(f"测试命令: {test_command}")
        sys.exit(0)

    try:
        run_loop(
            target_dir=str(target),
            agent=args.agent,
            max_rounds=args.max_rounds,
            max_retries=args.max_retries,
            no_backup=args.no_backup,
            sleep_between=args.sleep,
            timeout=args.timeout,
            agent_args=agent_args,
            keep_backups=args.keep_backups,
            replan=args.replan,
            dry_run=args.dry_run,
            no_commit=args.no_commit,
            test_command_override=args.test_command,
            task_ids=task_ids,
        )
    except KeyboardInterrupt:
        print("\n\n⏹ 用户中断,退出。")
        logger.warning("用户中断,退出。")
        # 中断时尽力恢复 stash（如果有的话）
        try:
            git_stash_pop(str(target))
        except Exception:
            pass


if __name__ == "__main__":
    main()
