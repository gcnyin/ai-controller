"""任务列表管理 —— 生成/解析/保存/加载/标记任务列表。"""

import re
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List

from . import C, cprint
from .logger import get_logger
from .agent import call_agent
from .prompts import PLAN_PROMPT

TASK_FILE = "AI-TASKS.md"


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
