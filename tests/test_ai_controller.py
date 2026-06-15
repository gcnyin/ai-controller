"""ai_controller 核心函数单元测试。

覆盖以下纯逻辑函数：
- _try_parse_json：JSON 解析与容错
- _extract_json_tasks：从 agent 输出中提取 JSON 任务列表
- parse_changelog_for_resume：解析 changelog 获取恢复进度
- check_ext_filter：文件后缀过滤
- load_task_list / save_task_list / mark_task_done / get_next_pending_task：任务列表管理
- parse_summary / extract_model_hint / build_ext_filter_arg / build_task_prompt：工具函数
- call_agent：subprocess 调用的 mock 测试
"""

import os
import json
import time
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

import ai_controller as ac


# ═══════════════════════════════════════════════════════════════════════
# _try_parse_json
# ═══════════════════════════════════════════════════════════════════════

class TestTryParseJson:
    """测试 JSON 解析逻辑，包含正常 JSON 和常见格式错误。"""

    def test_valid_json_with_tasks_key(self):
        """正确 JSON，含 "tasks" 键。"""
        data = '{"tasks": [{"id": 1, "title": "修复 bug"}]}'
        result = ac._try_parse_json(data)
        assert result == [{"id": 1, "title": "修复 bug"}]

    def test_valid_json_as_list(self):
        """正确 JSON，直接是数组。"""
        data = '[{"id": 1, "title": "修复 bug"}]'
        result = ac._try_parse_json(data)
        assert result == [{"id": 1, "title": "修复 bug"}]

    def test_valid_json_without_tasks(self):
        """JSON 对象但无 tasks 键 —— 返回 None。"""
        data = '{"summary": "一切正常"}'
        result = ac._try_parse_json(data)
        assert result is None

    def test_empty_tasks_array(self):
        """任务列表为空数组。"""
        data = '{"tasks": []}'
        result = ac._try_parse_json(data)
        assert result == []

    def test_trailing_comma_in_object(self):
        """JSON 尾部逗号 —— 应被自动修复。"""
        data = '{"tasks": [{"id": 1, "title": "OK"},],}'
        result = ac._try_parse_json(data)
        assert result == [{"id": 1, "title": "OK"}]

    def test_trailing_comma_in_array(self):
        """数组尾部逗号 —— 应被自动修复。"""
        data = '{"tasks": [{"id": 1},]}'
        result = ac._try_parse_json(data)
        assert result == [{"id": 1}]

    def test_completely_invalid_json(self):
        """完全无法解析的字符串 —— 返回 None。"""
        result = ac._try_parse_json("这不是 JSON")
        assert result is None

    def test_non_dict_non_list_value(self):
        """有效 JSON 但不是 dict 也不是 list —— 返回 None。"""
        result = ac._try_parse_json('"just a string"')
        assert result is None

    def test_unicode_in_json(self):
        """JSON 中含中文等 unicode 字符。"""
        data = '{"tasks": [{"id": 1, "title": "修复崩溃问题", "description": "在加载时检查NULL"}]}'
        result = ac._try_parse_json(data)
        assert result == [{"id": 1, "title": "修复崩溃问题", "description": "在加载时检查NULL"}]

    def test_multi_tasks_with_priority(self):
        """多个任务，带优先级。"""
        data = json.dumps({
            "tasks": [
                {"id": 1, "priority": "high", "type": "修复类", "title": "崩溃"},
                {"id": 2, "priority": "medium", "type": "功能开发类", "title": "新功能"},
                {"id": 3, "priority": "low", "type": "性能优化类", "title": "提速"},
            ],
            "summary": "需要全面改进",
        })
        result = ac._try_parse_json(data)
        assert len(result) == 3
        assert result[0]["priority"] == "high"


# ═══════════════════════════════════════════════════════════════════════
# _extract_json_tasks
# ═══════════════════════════════════════════════════════════════════════

class TestExtractJsonTasks:
    """测试从 agent 多格式输出中提取 JSON 任务列表。"""

    def test_markdown_code_block_with_json_tag(self):
        """Markdown 代码块，带 ```json 标记。"""
        text = textwrap.dedent("""\
            这是分析结果：
            ```json
            {
              "tasks": [
                {"id": 1, "title": "修 bug"}
              ],
              "summary": "需要修复"
            }
            ```
            以上是任务列表。
        """)
        result = ac._extract_json_tasks(text)
        assert result == [{"id": 1, "title": "修 bug"}]

    def test_markdown_code_block_without_tag(self):
        """Markdown 代码块，不带语言标记（仅 ```）。"""
        text = textwrap.dedent("""\
            ```
            {
              "tasks": [
                {"id": 2, "title": "加测试"}
              ]
            }
            ```
        """)
        result = ac._extract_json_tasks(text)
        assert result == [{"id": 2, "title": "加测试"}]

    def test_bare_json_in_text(self):
        """JSON 直接嵌入文本中，无 markdown 代码块。"""
        text = '好的，这是任务列表 {"tasks": [{"id": 1, "title": "test"}], "summary": "ok"} 谢谢'
        result = ac._extract_json_tasks(text)
        assert result == [{"id": 1, "title": "test"}]

    def test_no_json_found(self):
        """文本中不含任何 JSON 任务列表。"""
        result = ac._extract_json_tasks("这是一段普通的文字回复")
        assert result is None

    def test_json_array_without_tasks_key(self):
        """JSON 是数组但不是通过 tasks 键组织的 —— 直接解析。"""
        text = '{"tasks": [{"id": 5}]}'
        result = ac._extract_json_tasks(text)
        assert result == [{"id": 5}]

    def test_first_match_wins_with_multiple_blocks(self):
        """多个 JSON 块时，取第一个有效匹配。"""
        text = textwrap.dedent("""\
            ```json
            {"tasks": [{"id": 10, "title": "第一个"}]}
            ```
            补充说明...
            ```json
            {"tasks": [{"id": 20, "title": "第二个"}]}
            ```
        """)
        result = ac._extract_json_tasks(text)
        assert result == [{"id": 10, "title": "第一个"}]

    def test_multiline_json_with_indentation(self):
        """带缩进的多行 JSON。"""
        text = textwrap.dedent("""\
            ```json
            {
              "tasks": [
                {
                  "id": 1,
                  "priority": "high",
                  "type": "修复类",
                  "title": "修复内存泄漏",
                  "description": "长描述..."
                }
              ],
              "summary": "需要修复"
            }
            ```
        """)
        result = ac._extract_json_tasks(text)
        assert len(result) == 1
        assert result[0]["type"] == "修复类"


# ═══════════════════════════════════════════════════════════════════════
# parse_changelog_for_resume
# ═══════════════════════════════════════════════════════════════════════

class TestParseChangelogForResume:
    """测试从 AI-CHANGELOG.md 解析恢复信息。"""

    def test_single_round_changelog(self, tmp_workspace):
        """单轮 changelog，正确解析轮次号和改动说明。"""
        content = textwrap.dedent("""\
            # AI 自迭代改动记录
            - 开始时间: 2026-01-01 10:00:00
            - Agent: pi
            ---
            ## Round 1 — 2026-01-01 10:05:00

            改动说明: 修复了空指针异常，增加了参数校验
        """)
        log_path = Path(tmp_workspace) / "AI-CHANGELOG.md"
        log_path.write_text(content, encoding="utf-8")
        result = ac.parse_changelog_for_resume(tmp_workspace)
        assert result == (1, "修复了空指针异常，增加了参数校验")

    def test_multiple_rounds_returns_last(self, tmp_workspace):
        """多轮 changelog，返回最后一轮的轮次号和说明。"""
        content = textwrap.dedent("""\
            # AI 自迭代改动记录
            ---
            ## Round 1 — 2026-01-01 10:00:00

            改动说明: 第一轮改动

            ## Round 2 — 2026-01-01 10:10:00

            改动说明: 第二轮改动

            ## Round 3 — 2026-01-01 10:20:00

            改动说明: 第三轮改动
        """)
        log_path = Path(tmp_workspace) / "AI-CHANGELOG.md"
        log_path.write_text(content, encoding="utf-8")
        result = ac.parse_changelog_for_resume(tmp_workspace)
        assert result == (3, "第三轮改动")

    def test_no_changelog_file(self, tmp_workspace):
        """目录中没有 AI-CHANGELOG.md 文件。"""
        result = ac.parse_changelog_for_resume(tmp_workspace)
        assert result is None

    def test_empty_changelog(self, tmp_workspace):
        """changelog 文件存在但无轮次记录。"""
        log_path = Path(tmp_workspace) / "AI-CHANGELOG.md"
        log_path.write_text("# 空 changelog\n\n无轮次记录\n", encoding="utf-8")
        result = ac.parse_changelog_for_resume(tmp_workspace)
        assert result is None

    def test_changelog_with_bold_summary(self, tmp_workspace):
        """改动说明用粗体标记（**改动说明**:）。"""
        content = textwrap.dedent("""\
            ## Round 5 — 2026-01-05 12:00:00

            **改动说明**: 优化了重要逻辑
        """)
        log_path = Path(tmp_workspace) / "AI-CHANGELOG.md"
        log_path.write_text(content, encoding="utf-8")
        result = ac.parse_changelog_for_resume(tmp_workspace)
        assert result == (5, "优化了重要逻辑")

    def test_changelog_with_chinese_colon(self, tmp_workspace):
        """改动说明使用中文冒号。"""
        content = textwrap.dedent("""\
            ## Round 2 — 2026-01-02 10:00:00

            改动说明：用中文冒号写的说明
        """)
        log_path = Path(tmp_workspace) / "AI-CHANGELOG.md"
        log_path.write_text(content, encoding="utf-8")
        result = ac.parse_changelog_for_resume(tmp_workspace)
        assert result == (2, "用中文冒号写的说明")

    def test_multiline_summary(self, tmp_workspace):
        """改动说明跨多行。"""
        content = textwrap.dedent("""\
            ## Round 1 — 2026-01-01 10:00:00

            改动说明: 第一行改动描述
            第二行补充说明
            第三行更多细节
        """)
        log_path = Path(tmp_workspace) / "AI-CHANGELOG.md"
        log_path.write_text(content, encoding="utf-8")
        result = ac.parse_changelog_for_resume(tmp_workspace)
        assert result is not None
        # 多行说明被捕获（regex 使用 DOTALL）
        assert "第一行改动描述" in result[1]


# ═══════════════════════════════════════════════════════════════════════
# check_ext_filter
# ═══════════════════════════════════════════════════════════════════════

class TestCheckExtFilter:
    """测试文件后缀过滤逻辑。"""

    def test_no_filter_passes_all(self):
        """无过滤条件时，所有文件匹配。"""
        files = ["a.py", "b.ts", "c.md", "README"]
        matching, non_matching = ac.check_ext_filter(files, None)
        assert matching == files
        assert non_matching == []

    def test_filter_matching_only(self):
        """只保留指定后缀文件。"""
        allowed = {".py", ".ts"}
        files = ["src/main.py", "src/utils.py", "src/app.ts", "README.md", "package.json"]
        matching, non_matching = ac.check_ext_filter(files, allowed)
        assert sorted(matching) == ["src/app.ts", "src/main.py", "src/utils.py"]
        assert sorted(non_matching) == ["README.md", "package.json"]

    def test_no_files_match_filter(self):
        """无文件匹配过滤条件。"""
        allowed = {".rs"}
        files = ["main.py", "lib.js"]
        matching, non_matching = ac.check_ext_filter(files, allowed)
        assert matching == []
        assert sorted(non_matching) == ["lib.js", "main.py"]

    def test_empty_file_list(self):
        """空文件列表。"""
        allowed = {".py"}
        matching, non_matching = ac.check_ext_filter([], allowed)
        assert matching == []
        assert non_matching == []

    def test_all_match(self):
        """全部匹配。"""
        allowed = {".py"}
        files = ["a.py", "b.py", "c/d/e.py"]
        matching, non_matching = ac.check_ext_filter(files, allowed)
        assert matching == files
        assert non_matching == []

    def test_empty_allowed_set_means_no_filter(self):
        """空集合等同于 None，全部视为匹配。"""
        files = ["a.py", "b.md"]
        matching, non_matching = ac.check_ext_filter(files, set())
        assert matching == files
        assert non_matching == []

    def test_files_with_no_extension(self):
        """无后缀文件。"""
        allowed = {".py"}
        files = ["Makefile", "Dockerfile", "main.py"]
        matching, non_matching = ac.check_ext_filter(files, allowed)
        assert matching == ["main.py"]
        assert sorted(non_matching) == ["Dockerfile", "Makefile"]


# ═══════════════════════════════════════════════════════════════════════
# save_task_list / load_task_list / mark_task_done / get_next_pending_task
# ═══════════════════════════════════════════════════════════════════════

class TestTaskListIO:
    """测试任务列表的保存、加载、标记完成、获取待执行任务。"""

    def test_save_and_load_roundtrip(self, tmp_workspace, sample_tasks):
        """保存再加载，任务内容保持一致。"""
        ac.save_task_list(tmp_workspace, sample_tasks)
        loaded = ac.load_task_list(tmp_workspace)
        assert loaded is not None
        assert len(loaded) == 3
        # 检查 id 和 status
        ids = {t["id"] for t in loaded}
        assert ids == {1, 2, 3}
        statuses = {t["id"]: t["status"] for t in loaded}
        assert statuses[1] == "pending"
        assert statuses[2] == "pending"
        assert statuses[3] == "done"

    def test_load_nonexistent_file(self, tmp_workspace):
        """加载不存在的任务文件返回 None。"""
        result = ac.load_task_list(tmp_workspace)
        assert result is None

    def test_load_empty_tasks(self, tmp_workspace):
        """加载空任务列表文件 —— 无任务项时返回 None。"""
        task_file = Path(tmp_workspace) / "AI-TASKS.md"
        task_file.write_text("# AI 任务列表\n\n共 0 个任务\n\n", encoding="utf-8")
        result = ac.load_task_list(tmp_workspace)
        # load_task_list 在 task 列表为空时返回 None（空列表为 falsy）
        assert result is None

    def test_mark_task_done(self, tmp_workspace, sample_tasks):
        """标记任务为已完成。"""
        ac.save_task_list(tmp_workspace, sample_tasks)
        ac.mark_task_done(tmp_workspace, 2, 7)
        loaded = ac.load_task_list(tmp_workspace)
        task2 = next(t for t in loaded if t["id"] == 2)
        assert task2["status"] == "done"
        assert task2["completed_round"] == 7

    def test_mark_nonexistent_task(self, tmp_workspace, sample_tasks):
        """标记不存在的任务 ID 不影响现有数据。"""
        ac.save_task_list(tmp_workspace, sample_tasks)
        ac.mark_task_done(tmp_workspace, 999, 1)
        loaded = ac.load_task_list(tmp_workspace)
        assert len(loaded) == 3

    def test_get_next_pending_task_first(self, tmp_workspace, sample_tasks):
        """获取第一个待执行任务。"""
        ac.save_task_list(tmp_workspace, sample_tasks)
        task = ac.get_next_pending_task(tmp_workspace)
        assert task is not None
        assert task["id"] == 1  # 第一个 pending 任务

    def test_get_next_pending_task_after_marking_done(self, tmp_workspace, sample_tasks):
        """标记第一个完成后，获取下一个待执行任务。"""
        ac.save_task_list(tmp_workspace, sample_tasks)
        ac.mark_task_done(tmp_workspace, 1, 1)
        task = ac.get_next_pending_task(tmp_workspace)
        assert task is not None
        assert task["id"] == 2

    def test_get_next_pending_task_all_done(self, tmp_workspace, sample_tasks):
        """全部完成时返回 None。"""
        ac.save_task_list(tmp_workspace, sample_tasks)
        ac.mark_task_done(tmp_workspace, 1, 1)
        ac.mark_task_done(tmp_workspace, 2, 2)
        task = ac.get_next_pending_task(tmp_workspace)
        assert task is None

    def test_get_next_pending_no_tasks_file(self, tmp_workspace):
        """无任务文件时返回 None。"""
        task = ac.get_next_pending_task(tmp_workspace)
        assert task is None


# ═══════════════════════════════════════════════════════════════════════
# parse_summary
# ═══════════════════════════════════════════════════════════════════════

class TestParseSummary:
    """测试从 agent 输出中提取 SUMMARY 行。"""

    def test_extract_summary_line(self):
        output = "做了很多改动\nSUMMARY: 修复了空指针异常\n其他内容"
        assert ac.parse_summary(output) == "修复了空指针异常"

    def test_summary_with_chinese_colon(self):
        output = "SUMMARY：优化了数据库查询性能"
        assert ac.parse_summary(output) == "优化了数据库查询性能"

    def test_no_summary_line(self):
        output = "这是一段没有 SUMMARY 的输出"
        result = ac.parse_summary(output)
        assert "未提供具体说明" in result

    def test_case_insensitive_summary(self):
        output = "summary: lowercase summary text"
        assert ac.parse_summary(output) == "lowercase summary text"

    def test_summary_with_extra_spaces(self):
        output = "SUMMARY:   前后有额外的空格   "
        assert ac.parse_summary(output) == "前后有额外的空格"

    def test_empty_summary(self):
        """SUMMARY 行存在但无实际内容 —— 触发 fallback 消息。"""
        output = "SUMMARY:"
        # 正则要求 SUMMARY: 后至少一个字符，无字符时走 fallback
        result = ac.parse_summary(output)
        assert "未提供具体说明" in result


# ═══════════════════════════════════════════════════════════════════════
# extract_model_hint
# ═══════════════════════════════════════════════════════════════════════

class TestExtractModelHint:
    """测试从 agent 参数中提取模型名称。"""

    def test_no_args(self):
        assert ac.extract_model_hint(None) == ""

    def test_empty_list(self):
        assert ac.extract_model_hint([]) == ""

    def test_model_with_space(self):
        args = ["--model", "claude-sonnet-4-20250514"]
        assert ac.extract_model_hint(args) == "claude-sonnet-4-20250514"

    def test_short_flag_m(self):
        args = ["-m", "gpt-4"]
        assert ac.extract_model_hint(args) == "gpt-4"

    def test_model_with_equals(self):
        args = ["--model=gpt-4-turbo"]
        assert ac.extract_model_hint(args) == "gpt-4-turbo"

    def test_short_flag_equals(self):
        args = ["-m=gpt-4o"]
        assert ac.extract_model_hint(args) == "gpt-4o"

    def test_last_model_wins(self):
        args = ["--model", "first", "-m", "second", "--model=third"]
        assert ac.extract_model_hint(args) == "third"

    def test_model_in_mixed_args(self):
        args = ["-p", "--model", "claude-3", "--verbose"]
        assert ac.extract_model_hint(args) == "claude-3"


# ═══════════════════════════════════════════════════════════════════════
# build_ext_filter_arg
# ═══════════════════════════════════════════════════════════════════════

class TestBuildExtFilterArg:
    """测试构建文件过滤 prompt 参数。"""

    def test_none_exts(self):
        assert ac.build_ext_filter_arg("pi", None) is None

    def test_empty_set(self):
        assert ac.build_ext_filter_arg("pi", set()) is None

    def test_single_extension(self):
        result = ac.build_ext_filter_arg("pi", {".py"})
        assert "只处理 .py 文件" in result
        assert "忽略其他文件类型" in result

    def test_multiple_extensions_sorted(self):
        result = ac.build_ext_filter_arg("pi", {".ts", ".js", ".py"})
        assert "只处理 .js, .py, .ts 文件" in result


# ═══════════════════════════════════════════════════════════════════════
# build_task_prompt
# ═══════════════════════════════════════════════════════════════════════

class TestBuildTaskPrompt:
    """测试为单个任务构建执行提示词。"""

    def test_basic_task(self):
        task = {
            "type": "修复类",
            "title": "修复空指针",
            "description": "在 foo.py 加入 null 检查",
        }
        result = ac.build_task_prompt(task)
        assert "修复类" in result
        assert "修复空指针" in result
        assert "在 foo.py 加入 null 检查" in result
        assert "当前任务" in result

    def test_task_without_type(self):
        task = {"title": "无类型任务", "description": "描述"}
        result = ac.build_task_prompt(task)
        assert "无类型任务" in result


# ═══════════════════════════════════════════════════════════════════════
# call_agent — mock 测试
# ═══════════════════════════════════════════════════════════════════════

class TestCallAgent:
    """对 call_agent 进行 mock 测试，不实际调用外部进程。"""

    def test_successful_call(self):
        """模拟 agent 正常返回，输出含 SUMMARY。"""
        mock_stdout = "修改了文件 a.py\nSUMMARY: 修复了空指针"
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (mock_stdout, "")

        with patch("subprocess.Popen", return_value=mock_proc):
            success, summary, raw_output, elapsed = ac.call_agent(
                "pi", "请修复 bug", "/tmp/test",
            )
            assert success is True
            assert "修复了空指针" in summary
            assert raw_output == mock_stdout
            assert elapsed >= 0

    def test_agent_returns_nonzero(self):
        """agent 返回非零退出码，且无有效 SUMMARY。"""
        mock_stdout = "something went wrong, no summary line"
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate.return_value = (mock_stdout, "")

        with patch("subprocess.Popen", return_value=mock_proc):
            success, summary, raw_output, elapsed = ac.call_agent(
                "pi", "请修复 bug", "/tmp/test",
            )
            assert success is False
            assert "异常退出" in summary
            assert "1" in summary  # 返回码

    def test_timeout(self):
        """模拟 agent 超时。"""
        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = ac.subprocess.TimeoutExpired(
            cmd=["pi"], timeout=600,
        )

        with patch("subprocess.Popen", return_value=mock_proc):
            success, summary, raw_output, elapsed = ac.call_agent(
                "pi", "请修复 bug", "/tmp/test", timeout=600,
            )
            assert success is False
            assert "超时" in summary

    def test_quiet_mode_suppresses_output(self):
        """静默模式不直接打印原始输出。"""
        mock_stdout = "some long output\n" * 10 + "SUMMARY: done"
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (mock_stdout, "")

        with patch("subprocess.Popen", return_value=mock_proc):
            success, summary, raw_output, elapsed = ac.call_agent(
                "pi", "修复 bug", "/tmp/test", quiet=True,
            )
            assert success is True
            assert "done" in summary
            assert raw_output == mock_stdout

    def test_with_extra_args(self):
        """extra_args 被正确传递给 subprocess。"""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = ("SUMMARY: ok", "")

        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            ac.call_agent(
                "pi", "修复 bug", "/tmp/test",
                extra_args=["--model", "gpt-4"],
            )
            # 验证 Popen 被调用，且 cmd_parts 包含额外参数
            args, _ = mock_popen.call_args
            cmd_parts = args[0]
            assert "--model" in cmd_parts
            assert "gpt-4" in cmd_parts
            # prompt 应该是最后一个参数
            assert "修复 bug" in cmd_parts[-1]

    def test_with_ext_filter(self):
        """带 ext_filter 时 prompt 被合并。"""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = ("SUMMARY: done", "")

        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            ac.call_agent(
                "pi", "原始 prompt", "/tmp/test",
                ext_filter="只处理 .py 文件",
            )
            args, _ = mock_popen.call_args
            cmd_parts = args[0]
            full_prompt = cmd_parts[-1]
            assert "只处理 .py 文件" in full_prompt
            assert "原始 prompt" in full_prompt

    def test_generic_exception(self):
        """模拟非超时、非中断的其他异常。"""
        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = RuntimeError("模拟的运行时错误")

        with patch("subprocess.Popen", return_value=mock_proc):
            success, summary, raw_output, elapsed = ac.call_agent(
                "pi", "修复 bug", "/tmp/test",
            )
            assert success is False
            assert "调用失败" in summary
            assert raw_output == ""


# ═══════════════════════════════════════════════════════════════════════
# get_changed_files
# ═══════════════════════════════════════════════════════════════════════

class TestGetChangedFiles:
    """测试获取改动文件列表，覆盖 git 路径和 fallback 路径。"""

    def test_git_status_parsing(self, tmp_workspace):
        """模拟 git status --porcelain 输出。"""
        # 创建 .git 目录使函数走 git 路径
        git_dir = Path(tmp_workspace) / ".git"
        git_dir.mkdir()

        mock_result = MagicMock()
        mock_result.stdout = (
            " M src/main.py\n"
            "A  src/new_file.py\n"
            "?? README.md\n"
            " M AI-CHANGELOG.md\n"  # 应被过滤（LOG_FILE）
        )

        with patch("subprocess.run", return_value=mock_result):
            files = ac.get_changed_files(tmp_workspace)
            assert "src/main.py" in files
            assert "src/new_file.py" in files
            assert "README.md" in files
            # AI-CHANGELOG.md 应被过滤
            assert "AI-CHANGELOG.md" not in files

    def test_git_status_with_rename(self, tmp_workspace):
        """git status 中重命名文件处理。"""
        git_dir = Path(tmp_workspace) / ".git"
        git_dir.mkdir()

        mock_result = MagicMock()
        mock_result.stdout = "R  old_name.py -> new_name.py\n"

        with patch("subprocess.run", return_value=mock_result):
            files = ac.get_changed_files(tmp_workspace)
            assert "new_name.py" in files

    def test_git_status_empty(self, tmp_workspace):
        """git status 无改动。"""
        git_dir = Path(tmp_workspace) / ".git"
        git_dir.mkdir()

        mock_result = MagicMock()
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            files = ac.get_changed_files(tmp_workspace)
            assert files == []

    def test_git_status_filters_backup_dir(self, tmp_workspace):
        """备份目录文件被过滤。"""
        git_dir = Path(tmp_workspace) / ".git"
        git_dir.mkdir()

        mock_result = MagicMock()
        mock_result.stdout = (
            " M src/main.py\n"
            f"?? {ac.BACKUP_DIR_NAME}/round0001_20260101_000000/README.md\n"
        )

        with patch("subprocess.run", return_value=mock_result):
            files = ac.get_changed_files(tmp_workspace)
            assert "src/main.py" in files
            assert len(files) == 1  # 备份目录项被过滤

    def test_fallback_with_timestamp(self, tmp_workspace):
        """非 git 目录，基于时间戳 fallback 检测改动。"""
        # 不创建 .git 目录，走 fallback 路径
        # 使用 os.utime 精确控制文件时间戳
        old_file = Path(tmp_workspace) / "old.py"
        old_file.write_text("# old")
        os.utime(old_file, (1000000000, 1000000000))  # 设置为过去时间

        new_file = Path(tmp_workspace) / "new.py"
        new_file.write_text("# new")
        os.utime(new_file, (2000000000, 2000000000))  # 设置为未来时间

        files = ac.get_changed_files(tmp_workspace, since_ts=1500000000)
        # new.py 的 mtime 晚于 since_ts，应该被检测到
        assert "new.py" in files
        assert "old.py" not in files

    def test_fallback_no_changes(self, tmp_workspace):
        """fallback 模式，无文件改动。"""
        files = ac.get_changed_files(tmp_workspace, since_ts=time.time() + 1000)
        assert files == []

    def test_no_git_and_no_timestamp(self, tmp_workspace):
        """既无 git 也不传 since_ts。"""
        files = ac.get_changed_files(tmp_workspace)
        assert files == []

    def test_git_subprocess_error_falls_back(self, tmp_workspace):
        """git 目录存在但 git 命令失败时回退到 fallback。"""
        git_dir = Path(tmp_workspace) / ".git"
        git_dir.mkdir()

        # git 命令抛出异常
        with patch("subprocess.run", side_effect=OSError("git not found")):
            files = ac.get_changed_files(tmp_workspace, since_ts=0)
            assert files == []  # 回退到空结果


# ═══════════════════════════════════════════════════════════════════════
# init_log / write_round_log
# ═══════════════════════════════════════════════════════════════════════

class TestLoggingFunctions:
    """测试 changelog 写入相关函数。"""

    def test_init_log_creates_file(self, tmp_workspace):
        """init_log 应创建 AI-CHANGELOG.md 文件。"""
        ac.init_log(tmp_workspace, "pi")
        log_path = Path(tmp_workspace) / "AI-CHANGELOG.md"
        assert log_path.is_file()
        content = log_path.read_text(encoding="utf-8")
        assert "AI 自迭代改动记录" in content
        assert "pi" in content

    def test_init_log_with_model_hint(self, tmp_workspace):
        """带 model hint 的 init_log。"""
        ac.init_log(tmp_workspace, "claude", "claude-sonnet-4")
        content = (Path(tmp_workspace) / "AI-CHANGELOG.md").read_text(encoding="utf-8")
        assert "claude" in content
        assert "claude-sonnet-4" in content

    def test_write_round_log_appends(self, tmp_workspace):
        """write_round_log 追加轮次记录。"""
        ac.init_log(tmp_workspace, "pi")
        ac.write_round_log(
            tmp_workspace, 1,
            "修复了空指针",
            ["src/main.py", "src/utils.py"],
            12.5,
        )
        content = (Path(tmp_workspace) / "AI-CHANGELOG.md").read_text(encoding="utf-8")
        assert "Round 1" in content
        assert "修复了空指针" in content
        assert "src/main.py" in content
        assert "src/utils.py" in content
        assert "12.5" in content  # 耗时

    def test_write_round_log_no_files(self, tmp_workspace):
        """无文件改动的轮次记录。"""
        ac.init_log(tmp_workspace, "pi")
        ac.write_round_log(tmp_workspace, 2, "无改动", [], 3.0)
        content = (Path(tmp_workspace) / "AI-CHANGELOG.md").read_text(encoding="utf-8")
        assert "Round 2" in content
        assert "无改动" in content
        assert "无（本轮无代码变更）" in content


# ═══════════════════════════════════════════════════════════════════════
# load_config —— 配置文件读取
# ═══════════════════════════════════════════════════════════════════════

class TestLoadConfig:
    """测试从目标目录加载 .ai-controller.toml/.yaml 配置文件。"""

    def test_no_config_file(self, tmp_workspace):
        """目标目录无配置文件，返回空字典。"""
        config = ac.load_config(tmp_workspace)
        assert config == {}

    def test_toml_config_full(self, tmp_workspace):
        """TOML 配置文件，包含所有参数。"""
        toml_content = """\
agent = "claude"
max_rounds = 5
ext = ".py,.ts"
timeout = 300
sleep = 1.5
no_backup = true
no_git = false
agent_args = "--model gpt-4"
resume = true
keep_backups = 10
no_plan = true
plan_only = false
dry_run = false
"""
        cfg_path = Path(tmp_workspace) / ".ai-controller.toml"
        cfg_path.write_text(toml_content, encoding="utf-8")
        config = ac.load_config(tmp_workspace)

        assert config["agent"] == "claude"
        assert config["max_rounds"] == 5
        assert config["ext"] == ".py,.ts"
        assert config["timeout"] == 300
        assert config["sleep"] == 1.5
        assert config["no_backup"] is True
        assert config["no_git"] is False
        assert config["agent_args"] == "--model gpt-4"
        assert config["resume"] is True
        assert config["keep_backups"] == 10
        assert config["no_plan"] is True
        assert config["plan_only"] is False
        assert config["dry_run"] is False

    def test_toml_config_partial(self, tmp_workspace):
        """TOML 配置文件，只包含部分参数。"""
        toml_content = """\
agent = "pi"
max_rounds = 20
timeout = 900
"""
        cfg_path = Path(tmp_workspace) / ".ai-controller.toml"
        cfg_path.write_text(toml_content, encoding="utf-8")
        config = ac.load_config(tmp_workspace)

        assert config["agent"] == "pi"
        assert config["max_rounds"] == 20
        assert config["timeout"] == 900
        # 未配置的参数不应出现
        assert "sleep" not in config
        assert "ext" not in config

    def test_yaml_config(self, tmp_workspace):
        """YAML 配置文件，参数读取正确。"""
        yaml_content = """\
agent: claude
max_rounds: 3
ext: ".py"
timeout: 120
sleep: 3.0
no_backup: true
no_git: true
agent_args: "-m claude-sonnet-4"
resume: false
keep_backups: 5
no_plan: false
plan_only: false
dry_run: true
"""
        # 需要 PyYAML
        pytest.importorskip("yaml")
        cfg_path = Path(tmp_workspace) / ".ai-controller.yaml"
        cfg_path.write_text(yaml_content, encoding="utf-8")
        config = ac.load_config(tmp_workspace)

        assert config["agent"] == "claude"
        assert config["max_rounds"] == 3
        assert config["ext"] == ".py"
        assert config["timeout"] == 120
        assert config["sleep"] == 3.0
        assert config["no_backup"] is True
        assert config["no_git"] is True
        assert config["agent_args"] == "-m claude-sonnet-4"
        assert config["resume"] is False
        assert config["keep_backups"] == 5
        assert config["plan_only"] is False
        assert config["dry_run"] is True

    def test_yaml_config_with_yml_extension(self, tmp_workspace):
        """.ai-controller.yml 也能被识别并加载。"""
        yaml_content = """\
agent: pi
max_rounds: 1
"""
        pytest.importorskip("yaml")
        cfg_path = Path(tmp_workspace) / ".ai-controller.yml"
        cfg_path.write_text(yaml_content, encoding="utf-8")
        config = ac.load_config(tmp_workspace)

        assert config["agent"] == "pi"
        assert config["max_rounds"] == 1

    def test_toml_priority_over_yaml(self, tmp_workspace):
        """同时存在 .toml 和 .yaml 时，优先读取 .toml。"""
        toml_content = """\
agent = "pi"
max_rounds = 7
"""
        yaml_content = """\
agent: claude
max_rounds: 3
timeout: 999
"""
        pytest.importorskip("yaml")
        (Path(tmp_workspace) / ".ai-controller.toml").write_text(toml_content, encoding="utf-8")
        (Path(tmp_workspace) / ".ai-controller.yaml").write_text(yaml_content, encoding="utf-8")
        config = ac.load_config(tmp_workspace)

        # TOML 优先
        assert config["agent"] == "pi"
        assert config["max_rounds"] == 7
        # timeout 来自 yaml 不会被读取（因为 toml 优先）
        assert "timeout" not in config

    def test_invalid_toml_returns_empty(self, tmp_workspace):
        """无效的 TOML 文件返回空字典。"""
        toml_content = "这不是有效的 TOML 格式 {{{{"
        (Path(tmp_workspace) / ".ai-controller.toml").write_text(toml_content, encoding="utf-8")
        config = ac.load_config(tmp_workspace)
        assert config == {}

    def test_invalid_yaml_returns_empty(self, tmp_workspace):
        """无效的 YAML 文件返回空字典。"""
        pytest.importorskip("yaml")
        yaml_content = ": : : messed up yaml"
        (Path(tmp_workspace) / ".ai-controller.yaml").write_text(yaml_content, encoding="utf-8")
        config = ac.load_config(tmp_workspace)
        assert config == {}

    def test_bare_yaml_not_dict_returns_empty(self, tmp_workspace):
        """YAML 内容是标量或列表等非字典类型，返回空字典。"""
        pytest.importorskip("yaml")
        yaml_content = "- item1\n- item2\n"
        (Path(tmp_workspace) / ".ai-controller.yaml").write_text(yaml_content, encoding="utf-8")
        config = ac.load_config(tmp_workspace)
        assert config == {}

    def test_unknown_keys_filtered(self, tmp_workspace):
        """配置文件中的未知键被忽略。"""
        toml_content = """\
agent = "pi"
unknown_param = "should be ignored"
another_unknown = 123
"""
        cfg_path = Path(tmp_workspace) / ".ai-controller.toml"
        cfg_path.write_text(toml_content, encoding="utf-8")
        config = ac.load_config(tmp_workspace)

        assert config["agent"] == "pi"
        assert "unknown_param" not in config
        assert "another_unknown" not in config
        assert len(config) == 1  # 仅 agent 通过

    def test_type_conversion_int_values(self, tmp_workspace):
        """数值类型参数被正确转为 int/float。"""
        toml_content = """\
max_rounds = 42
timeout = 1800
sleep = 2.5
keep_backups = 3
"""
        cfg_path = Path(tmp_workspace) / ".ai-controller.toml"
        cfg_path.write_text(toml_content, encoding="utf-8")
        config = ac.load_config(tmp_workspace)

        assert isinstance(config["max_rounds"], int)
        assert config["max_rounds"] == 42
        assert isinstance(config["timeout"], int)
        assert config["timeout"] == 1800
        assert isinstance(config["sleep"], float)
        assert config["sleep"] == 2.5
        assert isinstance(config["keep_backups"], int)
        assert config["keep_backups"] == 3
