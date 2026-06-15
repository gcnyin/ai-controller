#!/usr/bin/env python3
"""
AI 自迭代控制器 —— 调用 pi/opencode/claude/codex 让 AI 自动循环改进代码。

用法:
    python ai_controller.py <目录> --agent pi [选项]

示例:
    # pi 跑 10 轮
    python ai_controller.py ./my-project --agent pi --max-rounds 10

    # opencode 无限循环
    python ai_controller.py ./my-project --agent opencode --max-rounds 0

    # claude 只改 .py 文件
    python ai_controller.py ./my-project --agent claude --ext .py --max-rounds 5

    # codex 跑 3 轮
    python ai_controller.py ./my-project --agent codex --max-rounds 3

    # 从中断处恢复继续迭代
    python ai_controller.py ./my-project --agent pi --resume
"""

import os
import re
import sys
import time
import shutil
import shlex
import argparse
import textwrap
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple

BACKUP_DIR_NAME = ".ai-controller-backups"

# ─── Agent 配置 ────────────────────────────────────────────────────────

AGENTS = {
    "pi": {
        "cmd": "pi",
        "args": ["-p"],            # -p = non-interactive, print & exit
        "cwd_option": None,        # runs in cwd
    },
    "opencode": {
        "cmd": "opencode",
        "args": ["run"],
        "cwd_option": "--dir",
    },
    "claude": {
        "cmd": "claude",
        "args": ["-p", "--dangerously-skip-permissions"],
        "cwd_option": None,
    },
    "codex": {
        "cmd": "codex",
        "args": ["exec", "--full-auto"],
        "cwd_option": "-C",
    },
}

# ─── 提示词模板 ────────────────────────────────────────────────────────

TASK_PROMPT = textwrap.dedent("""\
    你是一个高级软件工程师，正在对一个代码库进行持续迭代改进。

    你的任务是：扫描整个代码库，找出当前优先级最高、价值最大的一个改进点，并直接实现它。

    ## 你需要做的事情

    每次运行，你都需要完成以下步骤：

    1. 全面扫描 — 浏览代码库的结构、关键模块、入口文件、README、依赖等
    2. 评估现状 — 找出项目的核心功能是什么、当前处于什么阶段、有哪些明显短板
    3. 选择改进 — 从下面列出的改进类型中，选出当前价值最大的一项
    4. 动手实现 — 直接写代码，不空谈

    ## 改进类型（按场景分类）

    ### A. 修复类（如果代码有明显问题）
    - 运行时错误、崩溃、逻辑 bug
    - 边界条件处理不当（空值、越界、除零等）
    - 资源泄漏（文件未关闭、连接未释放、内存泄漏）
    - 并发/竞态条件
    - 安全漏洞（注入、XSS、权限绕过、密钥泄露）
    - 错误的配置、错误的依赖版本

    ### B. 功能开发类（如果代码基本能跑，但缺少重要功能）
    - 补全核心功能：核心业务流程是否完整？是否有明显的功能空缺？
    - 增强用户体验：错误提示是否友好？是否有进度反馈/加载状态？
    - 添加实用工具：命令行补全、配置校验、调试模式、日志查看
    - 改善开发者体验：更好的 README、Makefile、脚本、pre-commit hook
    - 集成新能力：API 对接、插件系统、导出/导入、数据可视化

    ### C. 架构/质量类（如果代码能跑但不够好）
    - 重复代码合并
    - 职责分离（一个函数/类做了太多事）
    - 接口抽象（方便未来扩展）
    - 配置与代码分离
    - 单例/全局状态清理
    - 循环依赖解除

    ### D. 性能优化类
    - 算法复杂度优化
    - 缓存策略
    - 懒加载/按需加载
    - 数据库查询优化（N+1 问题）
    - 大文件/大数据处理优化

    ### E. 质量保障类
    - 为关键逻辑补充单元测试
    - 为易出错的函数补充边界测试
    - 添加集成测试/端到端测试
    - 改善错误处理和日志（方便排查问题）

    ### F. 文档/类型类
    - 补充缺失的 docstring/JSDoc/注释
    - 添加类型标注（TypeScript 类型、Python type hints）
    - 改善 README（安装说明、使用示例、API 文档）
    - 添加架构说明文档

    ## 优先级判断法则

    遵循以下优先级顺序，从高到低：

    1. 致命问题 — 程序根本跑不起来、数据会丢、安全有洞 → 立即修
    2. 核心功能缺口 — 项目名不副实，主要功能没做全 → 补上
    3. 高频使用痛点 — 用户/开发者每天都会碰到的问题 → 优先
    4. 低成本的显著改善 — 改动很小但效果很大的事 → 顺手做
    5. 技术债务 — 长期维护隐患（重复代码、无测试、无日志）→ 逐步清理
    6. 锦上添花 — 好的文档、好的错误提示、好的类型 → 有余力再做

    ## 输出要求（重要）

    改动完成后，你必须在输出的最后单独一行给出改动总结，格式：
    SUMMARY: <一句话中文说明你做了什么改动，以及为什么选这个>

    如果仔细分析后认为代码库已经非常完善，确实无需任何改动，输出：
    SUMMARY: 无需改动，代码库已完善
    （只有在你认真扫描并确认后，才能说无需改动）

    ## 行为准则

    - 使用中文回复，所有说明、注释、SUMMARY 必须用中文
    - 禁止使用 emoji，回复和代码注释中不要出现任何 emoji 符号
    - 一次只做一个改进，确保质量
    - 直接修改文件，不要只给建议
    - 保持改动最小化，不对无关部分动手
    - 确保改动后代码仍然可编译/可运行
    - 不改 .git/、node_modules/、.venv/ 等非项目目录
    - 不要重复之前已经做过的改动

    开始吧，先扫描代码库，然后选择最有价值的一件事来做。
    """).strip()



LOG_FILE = "AI-CHANGELOG.md"


def build_round_prompt(round_num: int, max_rounds: int, prev_summary: str = "") -> str:
    """构建每轮的提示词，注入当前轮次信息和上轮改动上下文"""
    parts = [TASK_PROMPT]

    # 注入轮次信息
    round_info = f"\n\n## 当前迭代上下文\n\n" \
                 f"这是第 {round_num} 轮"
    if max_rounds > 0:
        round_info += f" / 共 {max_rounds} 轮"
    round_info += "。"
    parts.append(round_info)

    # 注入上轮改动上下文，帮助 AI 了解历史、避免重复
    if prev_summary:
        parts.append(
            f"\n上一轮 AI 完成的改动: {prev_summary}\n"
            f"请不要再做相同的改动，继续寻找新的改进点。"
        )

    return "\n".join(parts)


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


# ─── 备份 ──────────────────────────────────────────────────────────────

def backup_all(target_dir: str, round_num: int) -> Optional[Path]:
    """备份整个目标目录"""
    backup_root = Path(target_dir) / BACKUP_DIR_NAME
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_folder = backup_root / f"round{round_num:04d}_{timestamp}"

    try:
        shutil.copytree(
            target_dir, backup_folder,
            ignore=shutil.ignore_patterns(
                BACKUP_DIR_NAME, ".git", "node_modules", "__pycache__",
                ".venv", "venv", "dist", "build", ".next",
            ),
            dirs_exist_ok=True,
        )
        return backup_folder
    except Exception as e:
        print(f"  ⚠ 备份失败: {e}")
        return None


# ─── 日志记录 ──────────────────────────────────────────────────────────

def parse_summary(output: str) -> str:
    """从 agent 输出中提取 SUMMARY 行"""
    import re
    # 匹配 SUMMARY: xxx 或 SUMMARY：xxx（中英文冒号都支持）
    m = re.search(r"SUMMARY[:：]\s*(.+)", output, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # 如果没有 SUMMARY 行，尝试用 git diff 的简短描述
    return "AI 完成了代码改进（未提供具体说明）"


def init_log(target_dir: str, agent: str, model_hint: str = ""):
    """初始化 changelog 文件"""
    log_path = Path(target_dir) / LOG_FILE
    if log_path.exists():
        return  # 追加模式，不覆盖
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    model_str = f" ({model_hint})" if model_hint else ""
    log_path.write_text(
        f"# AI 自迭代改动记录\n\n"
        f"- 开始时间: {ts}\n"
        f"- Agent: {agent}{model_str}\n"
        f"- 目标目录: {target_dir}\n\n"
        f"---\n\n",
        encoding="utf-8",
    )


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


def get_changed_files(target_dir: str, since_ts: float = 0) -> list[str]:
    """获取本轮改动的文件列表。

    优先使用 git status --porcelain（精确且快速）。
    如果没有 git 仓库，则回退到文件系统时间戳比较。

    Args:
        target_dir: 目标目录路径
        since_ts: fallback 模式下用于比较的时间戳，凡 mtime > since_ts 的文件视为改动过
    """
    target_path = Path(target_dir)

    # ── 优先：git 仓库 ──
    if (target_path / ".git").is_dir():
        try:
            r = subprocess.run(
                ["git", "-C", target_dir, "status", "--porcelain"],
                capture_output=True, text=True, timeout=10,
            )
            files = []
            for line in r.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                # git status --porcelain: "XY filename" -- X=staged, Y=unstaged
                # 取第 4 个字符开始的路径（处理重命名时是 "R  old -> new"）
                path = line[3:].strip()
                # 处理重命名格式: "old -> new"
                if " -> " in path:
                    path = path.split(" -> ")[-1]
                if path:
                    files.append(path)
            return files
        except Exception:
            pass

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
                fp = os.path.join(root, f)
                try:
                    if os.path.getmtime(fp) > since_ts:
                        rel = os.path.relpath(fp, target_dir)
                        changed.append(rel)
                except OSError:
                    pass
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
        pass
    return ""


# ─── 颜色 ──────────────────────────────────────────────────────────────

class C:
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    MAGENTA = "\033[35m"
    BOLD = "\033[1m"
    R = "\033[0m"


def cprint(msg: str, color: str = ""):
    print(f"{color}{msg}{C.R}")


# ─── Agent 调用 ────────────────────────────────────────────────────────

def call_agent(agent: str, prompt: str, target_dir: str,
               ext_filter: Optional[str] = None,
               timeout: int = 600,
               extra_args: Optional[list] = None) -> tuple[bool, str, str, float]:
    """
    调用 agent 进行一轮修改。
    返回 (success, summary, raw_output, elapsed_seconds)
    """
    cfg = AGENTS[agent]

    # 合并 prompt
    full_prompt = prompt
    if ext_filter:
        full_prompt = ext_filter + "\n\n" + prompt

    cmd_parts = [cfg["cmd"]] + cfg["args"]

    # 追加用户传入的额外参数（如 --model, --verbose）
    if extra_args:
        cmd_parts.extend(extra_args)

    # 处理 cwd
    if cfg["cwd_option"]:
        cmd_parts.extend([cfg["cwd_option"], target_dir])
        cwd = None
    else:
        cwd = target_dir

    cmd_parts.append(full_prompt)

    cprint(f"  🚀 执行: {' '.join(shlex.quote(str(p)) for p in cmd_parts[:4])} ...", C.CYAN)

    start = time.time()
    try:
        proc = subprocess.Popen(
            cmd_parts,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        # 使用 communicate() 替代 for 循环 + wait，这样才能真正落实超时。
        # for line in proc.stdout 会一直阻塞到 stdout 关闭，如果进程挂死，
        # 超时机制永远不会触发。communicate() 在内部用 select/线程同时处理
        # 读取和等待，是 Python 官方推荐的正确方式。
        stdout_data, _ = proc.communicate(timeout=timeout)
        elapsed = time.time() - start

        if stdout_data:
            print(stdout_data, end="", flush=True)

        summary = parse_summary(stdout_data)
        return proc.returncode == 0, summary, stdout_data, elapsed

    except subprocess.TimeoutExpired:
        proc.kill()
        # 超时后尝试收集子进程已产生的部分输出
        try:
            partial_stdout, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            partial_stdout = ""
        elapsed = time.time() - start
        if partial_stdout:
            print(partial_stdout, end="", flush=True)
        cprint(f"\n  Agent 超时（{timeout} 秒）", C.RED)
        return False, "Agent 执行超时", partial_stdout, elapsed
    except KeyboardInterrupt:
        proc.kill()
        proc.communicate(timeout=5)
        raise
    except Exception as e:
        elapsed = time.time() - start
        cprint(f"  Agent 调用失败: {e}", C.RED)
        return False, f"调用失败: {e}", "", elapsed


# ─── Git ────────────────────────────────────────────────────────────────

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
        pass


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
):
    print()
    cprint("╔══════════════════════════════════════════╗", C.CYAN)
    cprint("║      AI 自迭代控制器 v2.0               ║", C.CYAN)
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
    if is_git_repo(target_dir) and not no_git:
        cprint(f"  Git      : 自动 commit", C.BOLD)
    print()

    ext_filter = build_ext_filter_arg(agent, allowed_ext)
    init_log(target_dir, agent)

    # 检查工作区是否有未提交的改动，如有则警告用户
    if is_git_repo(target_dir) and not no_git and has_changes(target_dir):
        cprint("  ⚠ 警告: 工作区存在未提交的改动，将与 AI 改动混合记录", C.YELLOW)

    consecutive_noops = 0
    round_num = 0
    prev_summary = ""

    # --resume 模式：从 changelog 解析上次进度，从下一轮继续
    if resume:
        resume_info = parse_changelog_for_resume(target_dir)
        if resume_info is None:
            cprint("  ⚠ 无法解析 changelog 中的进度信息，从头开始。", C.YELLOW)
        else:
            last_round, last_summary = resume_info
            if max_rounds > 0 and last_round >= max_rounds:
                cprint(f"  ✓ 上次已完成 {last_round}/{max_rounds} 轮，无需恢复。", C.GREEN)
                return
            round_num = last_round       # 循环开头 +1 后从 last_round+1 开始
            prev_summary = last_summary  # 将上轮改动传入下一轮作为上下文
            cprint(f"  恢复模式 : 从第 {last_round + 1} 轮继续（上次完成 {last_round} 轮）", C.BOLD + C.CYAN)
            print()

    while True:
        round_num += 1

        if max_rounds > 0 and round_num > max_rounds:
            cprint(f"\n✓ 达到最大轮次 {max_rounds}，退出。", C.GREEN)
            break

        if consecutive_noops >= 3:
            cprint(f"\n✓ 连续 {consecutive_noops} 轮无改动，代码已稳定，退出。", C.GREEN)
            break

        cprint(f"\n{'─' * 55}", C.CYAN)
        cprint(f"  第 {round_num} 轮迭代{' (无限)' if max_rounds == 0 else f' / {max_rounds}'}", C.BOLD + C.CYAN)
        cprint(f"{'─' * 55}", C.CYAN)

        # 备份（每轮开始前）
        if not no_backup:
            backup_folder = backup_all(target_dir, round_num)
            if backup_folder:
                cprint(f"  💾 已备份到: {backup_folder}", C.GREEN)

        # 记录改动前的 git 状态
        git_repo = is_git_repo(target_dir) and not no_git

        # 记录时间戳，用于非 git 目录下的文件改动检测
        before_ts = time.time()

        # 调用 agent
        prompt = build_round_prompt(round_num, max_rounds, prev_summary)
        success, summary, raw_output, elapsed = call_agent(
            agent, prompt, target_dir, ext_filter, timeout, agent_args,
        )

        print()  # 换行

        # 无论 Agent 是否成功，都先检测文件改动。
        # Agent 可能在报错前已经修改了文件，这些改动不应被忽略。
        changed_files = get_changed_files(target_dir, before_ts)
        has_diff = bool(changed_files)

        if not success and not has_diff:
            # Agent 失败且无文件改动 —— 真正的失败，计入无操作次数
            cprint(f"  Agent 返回异常，等待后继续...", C.YELLOW)
            write_round_log(target_dir, round_num, summary, [], elapsed)
            consecutive_noops += 1
            prev_summary = ""
            time.sleep(sleep_between)
            continue

        if not success and has_diff:
            # Agent 失败但产生了文件改动 —— 部分成功，按正常改动处理
            cprint(f"  Agent 返回异常但仍有文件改动，继续处理...", C.YELLOW)

        # 以下处理有文件改动或无改动的正常情况
        if has_diff:
            # 校验文件后缀过滤规则：如果用户指定了 --ext，检查 Agent 是否遵守
            filtered_files, bad_files = check_ext_filter(changed_files, allowed_ext)
            if bad_files:
                cprint(f"  ⚠ Agent 修改了 {len(bad_files)} 个非目标后缀文件 (--ext 过滤): "
                       f"{', '.join(bad_files[:5])}"
                       f"{' ...' if len(bad_files) > 5 else ''}", C.YELLOW)
                # 将不匹配文件追加到改动说明中，记录在 changelog 里
                suffix_note = f" [注意: Agent 同时修改了 {len(bad_files)} 个非目标后缀文件: " \
                              f"{', '.join(bad_files[:5])}"
                if len(bad_files) > 5:
                    suffix_note += f" ..."
                suffix_note += "]"
                summary = summary + suffix_note

            # 关键顺序说明：
            # 1. get_git_diff_summary 最先 —— 避免 changelog 自身的变更污染 diff 摘要
            # 2. write_round_log 在 commit 之前 —— 确保当轮 changelog 随改动一起提交
            # 3. git_commit 最后 —— 将所有改动（含 changelog）一并提交
            if git_repo:
                diff_stat = get_git_diff_summary(target_dir)
            write_round_log(target_dir, round_num, summary, changed_files, elapsed)
            if git_repo:
                git_commit(target_dir, round_num)
                if diff_stat:
                    cprint(f"  ✓ 本轮改动: {diff_stat}", C.GREEN)
                else:
                    cprint(f"  ✓ 已提交改动", C.GREEN)
            else:
                cprint(f"  ✓ 本轮修改 {len(changed_files)} 个文件", C.GREEN)

            cprint(f"  AI 说明: {summary}", C.MAGENTA)
            cprint(f"  改动文件: {', '.join(changed_files[:5])}"
                   f"{' ...' if len(changed_files) > 5 else ''}", C.GREEN)

            prev_summary = summary
            consecutive_noops = 0
        else:
            cprint(f"  本轮无文件改动", C.YELLOW)
            cprint(f"  AI 说明: {summary}", C.MAGENTA)
            write_round_log(target_dir, round_num, summary, [], elapsed)
            consecutive_noops += 1
            prev_summary = ""

        cprint(f"  ⏳ 等待 {sleep_between}s...", C.CYAN)
        time.sleep(sleep_between)


# ─── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI 自迭代控制器 — 调用外部 Agent 持续改进代码",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            示例:
              python ai_controller.py ./my-project --agent pi --max-rounds 10
              python ai_controller.py ./my-project --agent opencode --max-rounds 0
              python ai_controller.py ./my-project --agent claude --ext .py --max-rounds 5
              python ai_controller.py ./my-project --agent codex --max-rounds 3
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

    args = parser.parse_args()

    target = Path(args.directory).resolve()
    if not target.is_dir():
        cprint(f"错误: 目录不存在: {args.directory}", C.RED)
        sys.exit(1)

    # 检查 agent 是否可用
    agent_cmd = AGENTS[args.agent]["cmd"]
    if shutil.which(agent_cmd) is None:
        cprint(f"错误: 找不到 {agent_cmd} 命令，请确认 {args.agent} 已安装", C.RED)
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
        )
    except KeyboardInterrupt:
        cprint("\n\n⏹ 用户中断，退出。", C.YELLOW)


if __name__ == "__main__":
    main()
