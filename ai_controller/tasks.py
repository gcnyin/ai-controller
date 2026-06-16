"""任务列表管理 —— 生成/解析/保存/加载/标记任务列表。"""

import logging
import re
import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any


from .agent import call_agent
from .prompts import PLAN_PROMPT

logger = logging.getLogger(__name__)

TASK_FILE = "AI-TASKS.md"
TASK_FILE_BAK = "AI-TASKS.md.bak"  # 旧常量，保留兼容


def generate_task_list(agent: str, target_dir: str, ext_filter: Optional[str],
                       timeout: int, agent_args: Optional[list]) -> Optional[List[dict]]:
    """
    让 AI 扫描代码库并生成完整任务列表。
    返回解析后的任务列表，失败返回 None。
    """
    print(f"\n{'─' * 55}")
    print(f"  📋 规划阶段：扫描代码库，生成任务列表...")
    print(f"{'─' * 55}")

    success, summary, raw_output, elapsed = call_agent(
        agent, PLAN_PROMPT, target_dir, ext_filter, timeout, agent_args,
        quiet=True,
    )

    print(f"  ⏱ 规划耗时 {elapsed:.1f}s")

    if not success:
        logger.warning(f"规划失败: {summary}")
        return None

    # 从输出中提取 JSON（_extract_json_tasks 内置了多层解析策略）
    tasks = _extract_json_tasks(raw_output)
    if tasks is None:
        logger.warning("无法解析任务列表，将回退到逐轮模式")
        return None

    return tasks


def backup_task_file(target_dir: str):
    """备份现有 AI-TASKS.md 为 AI-TASKS.md.bak.YYYYMMDD_HHMMSS（用于 --replan）。"""
    src = Path(target_dir) / TASK_FILE
    if not src.is_file():
        return
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_name = f"{TASK_FILE}.{timestamp}.bak"
    dst = Path(target_dir) / bak_name
    shutil.copy2(src, dst)
    logger.info(f"已备份旧任务列表: {bak_name}")


def _extract_json_tasks(text: str) -> Optional[List[dict]]:
    """从 agent 输出中提取 JSON 任务列表。

    采用多层策略，按优先级依次尝试：
    1. 匹配 markdown 代码块（多种格式变体）
    2. 字符串感知的栈匹配找到外层 JSON 对象
    3. 直接解析整段文本
    """
    # ── 策略 1：匹配 markdown 代码块（多种变体）──
    code_block_patterns = [
        r'```(?:json)?\s*\n(.*?)\n```',         # 标准格式，前后有换行
        r'```(?:json)?\s*\n(.*?)```',             # 无尾部换行
        r'```(?:json)?(.*?)```',                    # 无换行
        r'~~~(?:json)?\s*\n(.*?)\n~~~',           # ~ 代码块
    ]
    for pat in code_block_patterns:
        for m in re.finditer(pat, text, re.DOTALL):
            result = _try_parse_json(m.group(1).strip())
            if result is not None:
                return result

    # ── 策略 2：字符串感知的栈匹配 ──
    # 遍历每个 { 位置，用字符串感知方式匹配对应的 }
    for start_match in re.finditer(r'\{', text):
        start = start_match.start()
        json_str = _safe_extract_json_substring(text, start)
        if json_str is not None and '"tasks"' in json_str:
            result = _try_parse_json(json_str)
            if result is not None:
                return result

    # ── 策略 3：直接解析整段文本 ──
    result = _try_parse_json(text)
    if result is not None:
        return result

    return None


def _safe_extract_json_substring(text: str, start: int) -> Optional[str]:
    """从 text[start] 开始，用字符串感知的栈匹配找到匹配的 }。

    正确处理 JSON 字符串内的 {、}、\\ 转义，不会因描述文本中的
    花括号导致匹配错误。
    """
    depth = 0
    i = start
    in_string = False
    escape = False

    while i < len(text):
        ch = text[i]

        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

        i += 1

    return None


def _try_parse_json(json_str: str) -> Optional[List[dict]]:
    """尝试解析 JSON 字符串并提取 tasks 数组。

    逐步清理策略：
    1. 直接 json.loads
    2. 去掉尾部逗号
    3. 去掉注释（// 和 /* */）
    4. 去掉所有非 JSON 前后文文本
    """
    # 策略 0：如果本身已经是纯 JSON，直接解析
    data = _json_loads_clean(json_str)
    if data is not None:
        if isinstance(data, dict) and "tasks" in data:
            return data["tasks"]
        if isinstance(data, list):
            return data

    # 策略 1：去掉尾部逗号
    cleaned = re.sub(r',\s*}', '}', json_str)
    cleaned = re.sub(r',\s*]', ']', cleaned)
    data = _json_loads_clean(cleaned)
    if data is not None:
        if isinstance(data, dict) and "tasks" in data:
            return data["tasks"]
        if isinstance(data, list):
            return data

    return None


def _json_loads_clean(s: str) -> Any:
    """尝试多种清理策略后解析 JSON。"""
    # 直接尝试
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # 尝试去掉注释（某些 AI 会加 // 注释）
    try:
        no_comments = re.sub(
            r'(?:^|[^:\\"\w])\/\/[^\n]*', '', s, flags=re.MULTILINE
        )
        return json.loads(no_comments)
    except json.JSONDecodeError:
        pass

    # 尝试去掉尾部逗号
    try:
        cleaned = re.sub(r',\s*}', '}', s)
        cleaned = re.sub(r',\s*]', ']', cleaned)
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 尝试去掉 JSON 前后的非 JSON 文本
    try:
        first_brace = s.find('{')
        last_brace = s.rfind('}')
        if first_brace >= 0 and last_brace > first_brace:
            trimmed = s[first_brace:last_brace + 1]
            return json.loads(trimmed)
    except json.JSONDecodeError:
        pass

    # 尝试修复控制字符问题
    try:
        cleaned = re.sub(r'[\x00-\x1f\x7f]', '', s)
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    return None


def save_task_list(target_dir: str, tasks: List[dict],
                   run_count: int = 1,
                   last_run: str = "",
                   global_round: int = 0):
    """将任务列表保存到 AI-TASKS.md。

    Args:
        run_count: 已运行次数
        last_run: 最后运行时间字符串（YYYY-MM-DD HH:MM:SS）
        global_round: 全局轮次计数
    """
    gen_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not last_run:
        last_run = gen_ts

    lines = [
        "# AI 任务列表",
        f"生成时间: {gen_ts}",
        f"运行次数: {run_count}",
        f"最后运行: {last_run}",
        f"全局轮次: {global_round}",
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
        # 按完成时间降序排列（最近完成的排前面）
        done.sort(key=lambda t: t.get("completed_time", ""), reverse=True)
        for t in done:
            tid = t.get("id", "?")
            title = t.get("title", "")
            round_num = t.get("completed_round", "?")
            done_ts = t.get("completed_time", "")
            if done_ts:
                lines.append(f"- [x] **#{tid}** {title} (Round {round_num}, {done_ts})")
            else:
                lines.append(f"- [x] **#{tid}** {title} (Round {round_num})")
            lines.append("")

    path = Path(target_dir) / TASK_FILE
    path.write_text("\n".join(lines), encoding="utf-8")


def load_task_metadata(target_dir: str) -> Dict[str, Any]:
    """从 AI-TASKS.md 头部解析运行元信息。

    Returns:
        dict 包含:
            - run_count: int (运行次数，默认 1)
            - last_run: str (最后运行时间，默认空)
            - global_round: int (全局轮次，默认 0)
            - gen_time: str (生成时间，默认空)
        文件不存在返回空字典。
    """
    path = Path(target_dir) / TASK_FILE
    if not path.is_file():
        return {}

    content = path.read_text(encoding="utf-8")
    metadata: Dict[str, Any] = {}

    patterns = {
        "gen_time": r'^生成时间:\s*(.+)$',
        "run_count": r'^运行次数:\s*(\d+)$',
        "last_run": r'^最后运行:\s*(.+)$',
        "global_round": r'^全局轮次:\s*(\d+)$',
    }

    for line in content.split("\n"):
        for key, pat in patterns.items():
            m = re.match(pat, line)
            if m:
                if key in ("run_count", "global_round"):
                    metadata[key] = int(m.group(1))
                else:
                    metadata[key] = m.group(1).strip()
                break

    metadata.setdefault("run_count", 1)
    metadata.setdefault("last_run", "")
    metadata.setdefault("global_round", 0)
    metadata.setdefault("gen_time", "")

    return metadata


def load_task_list(target_dir: str) -> Optional[List[dict]]:
    """从 AI-TASKS.md 加载任务列表，返回带状态的任务列表

    采用逐行解析替代单个复杂正则表达式，按前缀识别标记：
    - 以 "- [ ]" 或 "- [x]" 开头的行识别为任务条目
    - 以 "  "（两个空格）开头的行识别为描述续行
    - 以 "##" 开头的行识别为节标题（终止当前任务描述收集）

    已完成任务支持两种格式：
    - 旧格式: title (Round N)
    - 新格式: title (Round N, YYYY-MM-DD HH:MM)
    """
    path = Path(target_dir) / TASK_FILE
    if not path.is_file():
        return None

    content = path.read_text(encoding="utf-8")
    tasks = []
    current_task = None  # 当前正在构建的任务

    for line in content.split("\n"):
        # 识别任务条目行：- [ ] 或 - [x]
        task_match = re.match(r'- \[([ x])\] (.+)$', line)
        if task_match:
            # 保存上一个任务
            if current_task is not None:
                tasks.append(current_task)
                current_task = None

            status = "done" if task_match.group(1) == "x" else "pending"
            rest = task_match.group(2)

            # 提取 **#N**
            id_match = re.match(r'\*\*#(\d+)\*\*\s*(.*)$', rest)
            if not id_match:
                continue
            tid = int(id_match.group(1))
            tail = id_match.group(2)

            priority = "medium"
            ttype = ""
            title = ""
            completed_round = None
            completed_time = ""

            if status == "done":
                # 已完成任务格式: title (Round N) 或 title (Round N, YYYY-MM-DD HH:MM)
                # 先尝试新格式（带时间戳）
                round_match = re.search(
                    r'\s*\(Round (\d+),\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\)\s*$',
                    tail,
                )
                if round_match:
                    completed_round = int(round_match.group(1))
                    completed_time = round_match.group(2)
                    title = tail[:round_match.start()].strip()
                else:
                    # 回退到旧格式（仅 Round N）
                    round_match = re.search(r'\s*\(Round (\d+)\)\s*$', tail)
                    if round_match:
                        completed_round = int(round_match.group(1))
                        title = tail[:round_match.start()].strip()
                    else:
                        title = tail.strip()
            else:
                # 待执行任务格式: [priority] [type] title
                # 按顺序解析方括号标签，最多两个（priority, type）
                remaining = tail
                tags_found = 0
                while remaining.startswith("["):
                    bm = re.match(r'\[([^\]]+)\]\s*', remaining)
                    if not bm:
                        break
                    tag = bm.group(1)
                    remaining = remaining[bm.end():]
                    if tags_found == 0:
                        priority = tag
                    else:
                        ttype = tag
                    tags_found += 1
                title = remaining.strip()

            current_task = {
                "id": tid,
                "status": status,
                "priority": priority,
                "type": ttype,
                "title": title,
                "description": "",
                "completed_round": completed_round,
                "completed_time": completed_time,
            }
            continue

        # 识别描述续行（以两个空格开头且非空）
        if current_task is not None and line.startswith("  ") and line.strip():
            desc_line = line.strip()
            if current_task["description"]:
                current_task["description"] += "\n" + desc_line
            else:
                current_task["description"] = desc_line
            continue

    # 保存最后一个任务
    if current_task is not None:
        tasks.append(current_task)

    return tasks if tasks else None


def mark_task_done(target_dir: str, task_id: int, round_num: int,
                   tasks: Optional[List[dict]] = None,
                   run_count: int = 1,
                   last_run: str = "",
                   global_round: int = 0):
    """在任务列表中标记某个任务为已完成。

    若提供 tasks，则原地修改内存列表并写回文件（避免重复读取解析）。
    否则回退到从文件加载。

    Args:
        run_count, last_run, global_round: 传递给 save_task_list 的元信息
    """
    completed_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    if tasks is not None:
        for t in tasks:
            if t["id"] == task_id:
                t["status"] = "done"
                t["completed_round"] = round_num
                t["completed_time"] = completed_time
                break
        save_task_list(target_dir, tasks,
                       run_count=run_count, last_run=last_run,
                       global_round=global_round)
        return

    # 回退：从文件加载
    tasks = load_task_list(target_dir)
    if not tasks:
        return

    for t in tasks:
        if t["id"] == task_id:
            t["status"] = "done"
            t["completed_round"] = round_num
            t["completed_time"] = completed_time
            break

    save_task_list(target_dir, tasks,
                   run_count=run_count, last_run=last_run,
                   global_round=global_round)


def get_next_pending_task(target_dir: str,
                          tasks: Optional[List[dict]] = None) -> Optional[dict]:
    """获取下一个待执行的任务。

    若提供 tasks，则直接扫描内存列表（避免重复读取解析文件）。
    否则回退到从文件加载。
    """
    if tasks is not None:
        for t in tasks:
            if t.get("status") != "done":
                return t
        return None

    # 回退：从文件加载
    tasks = load_task_list(target_dir)
    if not tasks:
        return None

    for t in tasks:
        if t.get("status") != "done":
            return t
    return None
