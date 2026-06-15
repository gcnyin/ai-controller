#!/usr/bin/env python3
"""
AI 自迭代控制器 —— 调用 pi/opencode/claude/codex 让 AI 自动循环改进代码。

默认两阶段模式：先让 AI 扫描代码库生成完整任务列表，再逐条执行。

用法:
    python ai_controller.py <目录> --agent pi [选项]

示例:
    # 默认模式：先规划再执行
    python ai_controller.py ./my-project --agent pi --max-rounds 10

    # 只生成任务列表 AI-TASKS.md
    python ai_controller.py ./my-project --agent pi --plan-only

    # 传统模式：每轮 AI 自行选择
    python ai_controller.py ./my-project --agent pi --max-rounds 10 --no-plan

    # 恢复继续迭代
    python ai_controller.py ./my-project --agent pi --resume
"""

import os
import re
import sys
import time
import shutil
import shlex
import json
import logging
import argparse
import textwrap
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, List

BACKUP_DIR_NAME = ".ai-controller-backups"
TASK_FILE = "AI-TASKS.md"

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

PLAN_PROMPT = textwrap.dedent("""\
    你是一个高级软件工程师，需要对一个代码库进行全面评估。

    你的任务是：仔细扫描整个代码库，生成一份按优先级排序的改进任务列表。

    ## 改进类型（按场景分类）

    ### A. 修复类（如果代码有明显问题）

    ### B. 功能开发类（如果代码基本能跑，但缺少重要功能）

    ### C. 架构/质量类（如果代码能跑但不够好）

    ### D. 性能优化类

    ### E. 质量保障类

    ## 输出要求（重要）

    你必须使用以下 JSON 格式输出任务列表（不要包含 markdown 代码块标记，只输出纯 JSON）：

    {
      "tasks": [
        {"id": 1, "priority": "high", "type": "修复类", "title": "简短标题", "description": "详细描述要做什么、改哪个文件、为什么选这个"},
        {"id": 2, "priority": "medium", "type": "功能开发类", "title": "简短标题", "description": "详细描述"}
      ],
      "summary": "整体评估总结，用中文"
    }

    规则：
    - id 从 1 开始递增
    - priority 为 high / medium / low
    - title 不超过 30 个字
    - description 要说清楚改什么文件、做什么改动
    - 按优先级从高到低排列
    - 如果代码库已经很完善，tasks 可以为空数组
    - 禁止使用 emoji
    - 使用中文

    开始吧，先扫描代码库，然后生成完整的任务列表。
    """).strip()

TASK_PROMPT = textwrap.dedent("""\
    你是一个高级软件工程师，正在执行一个具体的改进任务。

    ## 当前任务

    {task_description}

    ## 行为准则

    - 直接修改文件，不要只给建议
    - 保持改动最小化，只做当前任务要求的事，不对无关部分动手
    - 确保改动后代码仍然可编译/可运行
    - 不改 .git/、node_modules/、.venv/ 等非项目目录
    - 使用中文回复，所有说明、注释必须用中文
    - 禁止使用 emoji

    改动完成后，在输出最后单独一行给出改动总结，格式：
    SUMMARY: <一句话中文说明你做了什么改动>
    """).strip()



LOG_FILE = "AI-CHANGELOG.md"
LOGGER_FILE = "ai-controller.log"

# ─── 日志系统 ──────────────────────────────────────────────────────────

_logger: Optional[logging.Logger] = None


class ColoredFormatter(logging.Formatter):
    """带 ANSI 颜色的控制台日志格式化器。

    根据日志级别自动添加颜色：DEBUG=青色, INFO=绿色,
    WARNING=黄色, ERROR=红色, CRITICAL=粗体红色。
    文件输出使用无颜色的纯文本。

    注意：使用 copy() 创建 record 副本以避免 ANSI 颜色码
    泄漏到后续的 handler（如 FileHandler）。
    """
    COLORS = {
        logging.DEBUG: "\033[36m",          # CYAN
        logging.INFO: "\033[32m",           # GREEN
        logging.WARNING: "\033[33m",        # YELLOW
        logging.ERROR: "\033[31m",          # RED
        logging.CRITICAL: "\033[1m\033[31m", # BOLD RED
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelno, "")
        if color:
            # 复制 record 避免颜色码泄漏到其他 handler
            record = logging.makeLogRecord(record.__dict__)
            record.levelname = f"{color}{record.levelname}{self.RESET}"
            record.msg = f"{color}{record.msg}{self.RESET}"
        return super().format(record)


def setup_logger(target_dir: str) -> logging.Logger:
    """配置双输出 logger：控制台（带颜色）+ 文件（纯文本）。

    控制台 handler：INFO 及以上级别，带 ANSI 颜色。
    文件 handler：DEBUG 及以上级别，无颜色，写入 ai-controller.log。

    Args:
        target_dir: 目标目录，日志文件将写入该目录下的 ai-controller.log
    Returns:
        配置好的 logger 实例
    """
    global _logger
    logger = logging.getLogger("ai-controller")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    # 控制台 handler — INFO 及以上，带颜色
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(ColoredFormatter("%(message)s"))
    logger.addHandler(ch)

    # 文件 handler — DEBUG 及以上，纯文本
    log_path = Path(target_dir) / LOGGER_FILE
    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)

    _logger = logger
    return logger


def get_logger() -> logging.Logger:
    """获取全局 logger（未初始化时返回一个基础 console logger）。"""
    global _logger
    if _logger is not None:
        return _logger
    # 回退：基础 console logger
    logger = logging.getLogger("ai-controller")
    if not logger.handlers:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger.addHandler(ch)
    logger.setLevel(logging.INFO)
    return logger


def build_task_prompt(task: dict) -> str:
    """为单个任务构建执行提示词"""
    desc = f"**[{task.get('type', '改进')}] {task.get('title', '')}**\n\n{task.get('description', '')}"
    return TASK_PROMPT.format(task_description=desc)


# ─── 任务列表管理 ───────────────────────────────────────────────────

def generate_task_list(agent: str, target_dir: str, ext_filter: Optional[str],
                       timeout: int, agent_args: Optional[list]) -> Optional[List[dict]]:
    """
    让 AI 扫描代码库并生成完整任务列表。
    返回解析后的任务列表，失败返回 None。
    """
    cprint(f"\n{'─' * 55}", C.CYAN)
    cprint(f"  📋 规划阶段：扫描代码库，生成任务列表...", C.BOLD + C.CYAN)
    cprint(f"{'─' * 55}", C.CYAN)

    success, summary, raw_output, elapsed = call_agent(
        agent, PLAN_PROMPT, target_dir, ext_filter, timeout, agent_args,
        quiet=True,
    )

    cprint(f"  ⏱ 规划耗时 {elapsed:.1f}s", C.CYAN)

    if not success:
        get_logger().warning(f"规划失败: {summary}")
        return None

    # 从输出中提取 JSON
    tasks = _extract_json_tasks(raw_output)
    if tasks is None:
        # 尝试把整段输出当 JSON 解析
        get_logger().warning("无法从 Agent 输出中提取任务 JSON，尝试直接解析...")
        tasks = _try_parse_json(raw_output)

    if tasks is None:
        get_logger().warning("无法解析任务列表，将回退到逐轮模式")
        return None

    return tasks


def _extract_json_tasks(text: str) -> Optional[List[dict]]:
    """从 agent 输出中提取 JSON 任务列表"""
    # 尝试匹配 JSON 块（可能被 markdown 包裹）
    # 匹配 ```json ... ``` 或无标记的 JSON
    patterns = [
        r'```(?:json)?\s*\n(.*?)\n```',  # markdown 代码块
        r'\{[\s\S]*"tasks"[\s\S]*\}',     # 直接 JSON 对象
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            json_str = m.group(1) if m.lastindex else m.group(0)
            result = _try_parse_json(json_str)
            if result:
                return result
    return None


def _try_parse_json(json_str: str) -> Optional[List[dict]]:
    """尝试解析 JSON 字符串并提取 tasks 数组"""
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        # 尝试修复常见问题：去掉尾部逗号等
        try:
            cleaned = re.sub(r',\s*}', '}', json_str)
            cleaned = re.sub(r',\s*]', ']', cleaned)
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return None

    if isinstance(data, dict) and "tasks" in data:
        return data["tasks"]
    if isinstance(data, list):
        return data
    return None


def save_task_list(target_dir: str, tasks: List[dict]):
    """将任务列表保存到 AI-TASKS.md"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# AI 任务列表",
        f"生成时间: {ts}",
        "",
        f"共 {len(tasks)} 个任务",
        "",
    ]

    # 按状态分组
    pending = []
    done = []
    for t in tasks:
        if t.get("status") == "done":
            done.append(t)
        else:
            pending.append(t)

    if pending:
        lines.append("## 待执行")
        lines.append("")
        for t in pending:
            prio = t.get("priority", "medium")
            tid = t.get("id", "?")
            title = t.get("title", "")
            desc = t.get("description", "")
            ttype = t.get("type", "")
            lines.append(f"- [ ] **#{tid}** [{prio}] [{ttype}] {title}")
            lines.append(f"  {desc}")
            lines.append("")

    if done:
        lines.append("## 已完成")
        lines.append("")
        for t in done:
            tid = t.get("id", "?")
            title = t.get("title", "")
            round_num = t.get("completed_round", "?")
            lines.append(f"- [x] **#{tid}** {title} (Round {round_num})")
            lines.append("")

    path = Path(target_dir) / TASK_FILE
    path.write_text("\n".join(lines), encoding="utf-8")


def load_task_list(target_dir: str) -> Optional[List[dict]]:
    """从 AI-TASKS.md 加载任务列表，返回带状态的任务列表"""
    path = Path(target_dir) / TASK_FILE
    if not path.is_file():
        return None

    content = path.read_text(encoding="utf-8")
    tasks = []

    # 解析 markdown 列表项
    # - [ ] **#1** [high] [修复类] 标题
    #   描述
    pattern = r'- \[([ x])\] \*\*#(\d+)\*\* (?:\[([^\]]+)\] )?(?:\[([^\]]+)\])? ?(.+?)(?:\n  (.+?))?(?=\n- |\n##|\Z)'

    for m in re.finditer(pattern, content, re.DOTALL):
        status = "done" if m.group(1) == "x" else "pending"
        tid = int(m.group(2))
        priority = m.group(3) or "medium"
        ttype = m.group(4) or ""
        title = m.group(5).strip()
        desc = m.group(6).strip() if m.group(6) else ""

        # 尝试从已完成项中提取 round 号
        completed_round = None
        if status == "done":
            round_match = re.search(r'Round (\d+)', m.group(0))
            if round_match:
                completed_round = int(round_match.group(1))

        tasks.append({
            "id": tid,
            "status": status,
            "priority": priority,
            "type": ttype,
            "title": title,
            "description": desc,
            "completed_round": completed_round,
        })

    return tasks if tasks else None


def mark_task_done(target_dir: str, task_id: int, round_num: int):
    """在任务列表中标记某个任务为已完成"""
    tasks = load_task_list(target_dir)
    if not tasks:
        return

    for t in tasks:
        if t["id"] == task_id:
            t["status"] = "done"
            t["completed_round"] = round_num
            break

    save_task_list(target_dir, tasks)


def get_next_pending_task(target_dir: str) -> Optional[dict]:
    """获取下一个待执行的任务"""
    tasks = load_task_list(target_dir)
    if not tasks:
        return None

    for t in tasks:
        if t.get("status") != "done":
            return t
    return None


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


def cleanup_old_backups(target_dir: str, keep_count: int):
    """删除旧备份，只保留最近 keep_count 个备份目录。

    备份目录按名称排序（名称含时间戳，自然顺序即为时间顺序），
    删除最旧的超出数量的目录。

    Args:
        target_dir: 目标目录路径
        keep_count: 保留的备份数量，<=0 表示不限制
    """
    if keep_count <= 0:
        return

    backup_root = Path(target_dir) / BACKUP_DIR_NAME
    if not backup_root.is_dir():
        return

    # 收集所有备份子目录，按名称排序（含时间戳，自然顺序即时间顺序）
    backups = sorted(
        [d for d in backup_root.iterdir() if d.is_dir()],
        key=lambda d: d.name,
    )

    if len(backups) <= keep_count:
        return

    # 删除最旧的超出部分
    to_delete = backups[:len(backups) - keep_count]
    for d in to_delete:
        try:
            shutil.rmtree(d)
        except Exception as e:
            print(f"  ⚠ 清理旧备份 {d.name} 失败: {e}")


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


def extract_model_hint(agent_args: Optional[list]) -> str:
    """从 agent 额外参数中提取模型信息，用于 changelog 头部显示。

    支持 --model <name> 和 -m <name> 两种写法。
    如果同时出现多个，取最后一个。
    """
    if not agent_args:
        return ""
    # 迭代查找 --model / -m，取最后出现的值
    hint = ""
    i = 0
    while i < len(agent_args):
        arg = agent_args[i]
        if arg in ("--model", "-m") and i + 1 < len(agent_args):
            hint = agent_args[i + 1]
            i += 2
            continue
        # 处理 --model=xxx 写法
        if arg.startswith("--model=") or arg.startswith("-m="):
            hint = arg.split("=", 1)[1]
        i += 1
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
                # 过滤控制器自身的管理文件
                if f == LOG_FILE:
                    continue
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
               extra_args: Optional[list] = None,
               quiet: bool = False) -> tuple[bool, str, str, float]:
    """
    调用 agent 进行一轮修改。
    返回 (success, summary, raw_output, elapsed_seconds)

    quiet=True 时不打印 agent 的原始输出（不打印 prompt 和冗余输出）。
    """
    cfg = AGENTS[agent]

    # 合并 prompt
    full_prompt = prompt
    if ext_filter:
        full_prompt = ext_filter + "\n\n" + prompt

    cmd_parts = [cfg["cmd"]]
    if extra_args:
        cmd_parts.extend(extra_args)
    cmd_parts.extend(cfg["args"])

    if cfg["cwd_option"]:
        cmd_parts.extend([cfg["cwd_option"], target_dir])
        cwd = None
    else:
        cwd = target_dir

    cmd_parts.append(full_prompt)

    if not quiet:
        cprint(f"  🚀 执行: {' '.join(shlex.quote(str(p)) for p in cmd_parts[:4])} ...", C.CYAN)
    else:
        # 静默模式只显示简短提示
        prompt_preview = prompt[:80].replace('\n', ' ')
        cprint(f"  🚀 {agent} 工作中... ({prompt_preview}...)", C.CYAN)

    start = time.time()
    try:
        proc = subprocess.Popen(
            cmd_parts,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        stdout_data, _ = proc.communicate(timeout=timeout)
        elapsed = time.time() - start

        # 静默模式不打印原始输出，只显示最后几行摘要
        if not quiet:
            if stdout_data:
                print(stdout_data, end="", flush=True)
        else:
            # 静默模式下只显示最后 5 行，避免刷屏
            if stdout_data:
                lines = stdout_data.strip().split('\n')
                tail = lines[-5:] if len(lines) > 5 else lines
                if tail:
                    print("\n".join(tail))

        summary = parse_summary(stdout_data)
        if proc.returncode != 0 and "未提供具体说明" in summary:
            summary = f"Agent 异常退出（返回码 {proc.returncode}），未提供改动说明"
        return proc.returncode == 0, summary, stdout_data, elapsed

    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            partial_stdout, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            partial_stdout = ""
        elapsed = time.time() - start
        if not quiet and partial_stdout:
            print(partial_stdout, end="", flush=True)
        cprint(f"\n  Agent 超时（{timeout} 秒）", C.RED)
        return False, "Agent 执行超时", partial_stdout, elapsed
    except KeyboardInterrupt:
        proc.kill()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
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


# ─── 单轮执行（run_loop 和 _run_legacy_loop 共用） ─────────────────

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
        tasks = generate_task_list(agent, target_dir, ext_filter, timeout, agent_args)
        if tasks is None:
            get_logger().warning("任务列表生成失败，回退到逐轮模式")
            _run_legacy_loop(
                target_dir, agent, max_rounds, allowed_ext,
                no_backup, no_git, sleep_between, timeout,
                agent_args, False, keep_backups, ext_filter,
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
        )
    except KeyboardInterrupt:
        cprint("\n\n⏹ 用户中断，退出。", C.YELLOW)
        get_logger().warning("用户中断，退出。")


if __name__ == "__main__":
    main()
