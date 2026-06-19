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
TASK_JSON_FILE = "AI-TASKS.json"


def generate_task_list(agent: str, target_dir: str,
                       timeout: int, agent_args: Optional[list]) -> tuple[Optional[List[dict]], Optional[str]]:
    """
    让 AI 扫描代码库并生成完整任务列表。
    返回解析后的任务列表，失败返回 None。
    """
    print(f"\n{'─' * 55}")
    print(f"  📋 规划阶段：扫描代码库，生成任务列表...")
    print(f"{'─' * 55}")

    success, summary, raw_output, elapsed = call_agent(
        agent, PLAN_PROMPT, target_dir, timeout, agent_args,
        quiet=True,
    )

    print(f"  ⏱ 规划耗时 {elapsed:.1f}s")

    if not success:
        logger.warning(f"规划失败: {summary}")
        return None

    # 从输出中提取 JSON（_extract_json_tasks 内置了多层解析策略）
    tasks, test_command = _extract_json_tasks(raw_output)
    if tasks is None:
        logger.warning("无法解析任务列表，将回退到逐轮模式")
        # ── 打印 AI 原始响应，方便用户排查 ──
        print()
        print("=" * 60)
        print("  [解析失败] AI 返回的原始响应如下:")
        print("=" * 60)
        print(raw_output)
        print("=" * 60)
        print("  [诊断] 请检查 AI 的返回是否包含合法 JSON（{ \"tasks\": [...] }）")
        print("  常见问题: JSON 格式错误、包含多余文本、花括号不匹配、尾部逗号等")
        print("=" * 60)
        print()
        logger.debug("AI 原始响应(DEBUG):\n%s", raw_output)
        return None, None

    return tasks, test_command


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


def _extract_json_tasks(text: str) -> tuple[Optional[List[dict]], Optional[str]]:
    """从 AI 输出中提取 JSON 任务列表和测试命令。

    返回 (tasks, test_command)，两者都可能为 None。
    假定 AI 输出纯 JSON，偶尔包裹在 markdown 代码块中。
    """
    text = text.strip()

    def _parse_json(json_str: str) -> tuple[Optional[List[dict]], Optional[str]]:
        """尝试从 JSON 字符串中解析 tasks 和 test_command。"""
        try:
            data = json.loads(json_str)
            if isinstance(data, dict) and "tasks" in data:
                tasks = data["tasks"]
                tc = data.get("test_command", "")
                # 空字符串视为无测试命令
                tc = tc if isinstance(tc, str) and tc.strip() else None
                return tasks, tc
            if isinstance(data, list):
                return data, None
        except json.JSONDecodeError:
            pass
        return None, None

    # ── 匹配 markdown 代码块 ──
    patterns = [
        r'```(?:json)?\s*\n(.*?)\n```',
        r'```(?:json)?\s*\n(.*?)```',
        r'```(?:json)?(.*?)```',
        r'~~~(?:json)?\s*\n(.*?)\n~~~',
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.DOTALL):
            tasks, tc = _parse_json(m.group(1).strip())
            if tasks is not None:
                return tasks, tc

    # ── 回退：取第一个 { 到最后一个 } ──
    a = text.find('{')
    b = text.rfind('}')
    if a >= 0 and b > a:
        tasks, tc = _parse_json(text[a:b + 1])
        if tasks is not None:
            return tasks, tc

    return None, None


def save_task_list(target_dir: str, tasks: List[dict],
                   run_count: int = 1,
                   last_run: str = "",
                   global_round: int = 0,
                   gen_time: str = "",
                   test_command: Optional[str] = None,
                   json_output: bool = False):
    """将任务列表保存到 AI-TASKS.md（以及 AI-TASKS.json）。

    Args:
        run_count: 已运行次数
        last_run: 最后运行时间字符串（YYYY-MM-DD HH:MM:SS）
        global_round: 全局轮次计数
        gen_time: 生成时间字符串，为空则使用当前时间
        test_command: 项目测试命令，为空则跳过
        json_output: 是否同时写入 AI-TASKS.json（机器可读格式）
    """
    gen_ts = gen_time if gen_time else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not last_run:
        last_run = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# AI 任务列表",
        f"生成时间: {gen_ts}",
        f"运行次数: {run_count}",
        f"最后运行: {last_run}",
        f"全局轮次: {global_round}",
    ]
    if test_command:
        lines.append(f"测试命令: {test_command}")
    lines.extend([
        "",
        f"共 {len(tasks)} 个任务",
        "",
    ])

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
    try:
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError as e:
        logger.warning("无法写入任务文件 %s: %s", path, e)

    # ── JSON 输出（机器可读）──
    if json_output:
        json_path = Path(target_dir) / TASK_JSON_FILE
        # 构建完整 JSON 结构（元信息 + 任务列表）
        payload = {
            "gen_time": gen_ts,
            "run_count": run_count,
            "last_run": last_run,
            "global_round": global_round,
            "tasks": tasks,
        }
        if test_command:
            payload["test_command"] = test_command
        try:
            json_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("无法写入 JSON 任务文件 %s: %s", json_path, e)


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
        "test_command": r'^测试命令:\s*(.+)$',
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
    metadata.setdefault("test_command", "")

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
                while tags_found < 2 and remaining.startswith("["):
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
                   global_round: int = 0,
                   gen_time: str = "",
                   json_output: bool = False):
    """在任务列表中标记某个任务为已完成。

    若提供 tasks，则原地修改内存列表并写回文件（避免重复读取解析）。
    否则回退到从文件加载。

    Args:
        run_count, last_run, global_round, gen_time: 传递给 save_task_list 的元信息
        json_output: 传递给 save_task_list，是否同时写入 JSON
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
                       global_round=global_round, gen_time=gen_time,
                       json_output=json_output)
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
                   global_round=global_round, gen_time=gen_time,
                   json_output=json_output)


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


# ── 自检：验证 JSON 任务输出 ────────────────────────────────────────

def _self_check():
    """验证 save_task_list 在 json_output=True 时正确写入 JSON 文件。"""
    import tempfile
    import os

    td = tempfile.mkdtemp()
    try:
        tasks_data = [
            {"id": 1, "status": "pending", "priority": "high",
             "type": "bug", "title": "Fix crash", "description": "",
             "completed_round": None, "completed_time": None},
        ]
        save_task_list(td, tasks_data, run_count=1, last_run="",
                       global_round=0, gen_time="2026-01-01 00:00:00",
                       test_command="pytest", json_output=True)

        # 验证 JSON 文件存在且内容正确
        jp = Path(td) / TASK_JSON_FILE
        assert jp.is_file(), f"JSON 文件未创建: {jp}"
        data = json.loads(jp.read_text(encoding="utf-8"))
        assert data["run_count"] == 1
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["id"] == 1
        assert data["test_command"] == "pytest"

        # 验证 json_output=False 不创建 JSON 文件
        td2 = tempfile.mkdtemp()
        try:
            save_task_list(td2, tasks_data, json_output=False)
            assert not (Path(td2) / TASK_JSON_FILE).is_file(), (
                "json_output=False 时不应创建 JSON 文件")
        finally:
            import shutil as _shutil
            _shutil.rmtree(td2, ignore_errors=True)
    finally:
        import shutil as _shutil
        _shutil.rmtree(td, ignore_errors=True)

    print("tasks._self_check: OK")


if __name__ == "__main__":
    _self_check()
