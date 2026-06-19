"""ai_controller 核心函数单元测试。

覆盖以下纯逻辑函数：
- _extract_json_tasks：从 agent 输出中提取 JSON 任务列表
- load_task_list / save_task_list / load_task_metadata / mark_task_done / get_next_pending_task：任务列表管理
- parse_summary / extract_model_hint / build_task_prompt：工具函数
- call_agent：subprocess 调用的 mock 测试
"""

import os
import json
import subprocess
import time
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

import ai_controller as ac


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
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks == [{"id": 1, "title": "修 bug"}]
        assert tc is None

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
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks == [{"id": 2, "title": "加测试"}]
        assert tc is None

    def test_bare_json_in_text(self):
        """JSON 直接嵌入文本中，无 markdown 代码块。"""
        text = '好的，这是任务列表 {"tasks": [{"id": 1, "title": "test"}], "summary": "ok"} 谢谢'
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks == [{"id": 1, "title": "test"}]
        assert tc is None

    def test_no_json_found(self):
        """文本中不含任何 JSON 任务列表。"""
        tasks, tc = ac._extract_json_tasks("这是一段普通的文字回复")
        assert tasks is None
        assert tc is None

    def test_json_array_without_tasks_key(self):
        """JSON 是数组但不是通过 tasks 键组织的 —— 直接解析。"""
        text = '{"tasks": [{"id": 5}]}'
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks == [{"id": 5}]
        assert tc is None

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
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks == [{"id": 10, "title": "第一个"}]
        assert tc is None

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
        tasks, tc = ac._extract_json_tasks(text)
        assert len(tasks) == 1
        assert tasks[0]["type"] == "修复类"
        assert tc is None



    def test_nested_objects_in_tasks(self):
        """tasks 内包含嵌套对象（测试栈匹配正确处理嵌套）。"""
        text = textwrap.dedent("""\
            规划结果：
            {
              "tasks": [
                {
                  "id": 1,
                  "priority": "high",
                  "type": "功能开发类",
                  "title": "添加功能",
                  "description": "描述信息"
                }
              ],
              "summary": "需要改进"
            }
        """)
        tasks, tc = ac._extract_json_tasks(text)
        assert len(tasks) == 1
        assert tasks[0]["id"] == 1
        assert tc is None

    def test_extract_test_command_from_json(self):
        """从 JSON 中提取 test_command 字段。"""
        text = '{"tasks": [{"id": 1}], "summary": "ok", "test_command": "pytest tests/ -v"}'
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks == [{"id": 1}]
        assert tc == "pytest tests/ -v"

    def test_extract_empty_test_command(self):
        """test_command 为空字符串时返回 None。"""
        text = '{"tasks": [{"id": 1}], "summary": "ok", "test_command": ""}'
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks == [{"id": 1}]
        assert tc is None

    def test_extract_test_command_not_present(self):
        """JSON 不含 test_command 字段时返回 None。"""
        text = '{"tasks": [{"id": 1}], "summary": "ok"}'
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks == [{"id": 1}]
        assert tc is None


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

    def test_save_and_load_with_metadata(self, tmp_workspace, sample_tasks):
        """带元信息保存再加载，元信息正确持久化。"""
        ac.save_task_list(tmp_workspace, sample_tasks,
                          run_count=3, last_run="2026-06-15 17:30:00",
                          global_round=12)
        loaded = ac.load_task_list(tmp_workspace)
        assert loaded is not None
        assert len(loaded) == 3

        meta = ac.load_task_metadata(tmp_workspace)
        assert meta["run_count"] == 3
        assert meta["last_run"] == "2026-06-15 17:30:00"
        assert meta["global_round"] == 12

    def test_load_metadata_no_file(self, tmp_workspace):
        """无任务文件时 load_task_metadata 返回空字典。"""
        meta = ac.load_task_metadata(tmp_workspace)
        assert meta == {}

    def test_load_metadata_old_format_no_header(self, tmp_workspace):
        """旧格式（无元信息头部）返回默认值。"""
        content = textwrap.dedent("""\
            # AI 任务列表
            生成时间: 2026-01-01 10:00:00

            共 1 个任务

            ## 待执行
            - [ ] **#1** [high] [修复类] 修复 bug
              详细描述
        """)
        (Path(tmp_workspace) / "AI-TASKS.md").write_text(content, encoding="utf-8")
        meta = ac.load_task_metadata(tmp_workspace)
        assert meta["run_count"] == 1  # 默认值
        assert meta["last_run"] == ""
        assert meta["global_round"] == 0
        assert meta["gen_time"] == "2026-01-01 10:00:00"

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
        # 新格式应包含 completed_time
        assert task2.get("completed_time", "") != ""

    def test_mark_task_done_with_metadata(self, tmp_workspace, sample_tasks):
        """带元信息标记完成，元信息持久化。"""
        ac.save_task_list(tmp_workspace, sample_tasks)
        ac.mark_task_done(tmp_workspace, 2, 7,
                          run_count=2, last_run="2026-06-15 18:00:00",
                          global_round=7)
        meta = ac.load_task_metadata(tmp_workspace)
        assert meta["run_count"] == 2
        assert meta["last_run"] == "2026-06-15 18:00:00"
        assert meta["global_round"] == 7

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

    def test_save_and_load_with_test_command(self, tmp_workspace, sample_tasks):
        """test_command 持久化到 AI-TASKS.md 头部，加载时可恢复。"""
        ac.save_task_list(tmp_workspace, sample_tasks,
                          test_command="pytest tests/ -v")
        meta = ac.load_task_metadata(tmp_workspace)
        assert meta["test_command"] == "pytest tests/ -v"

        # 文件内容中应包含测试命令
        content = (Path(tmp_workspace) / "AI-TASKS.md").read_text(encoding="utf-8")
        assert "测试命令: pytest tests/ -v" in content

    def test_save_without_test_command(self, tmp_workspace, sample_tasks):
        """无 test_command 时 AI-TASKS.md 不含测试命令行。"""
        ac.save_task_list(tmp_workspace, sample_tasks)
        meta = ac.load_task_metadata(tmp_workspace)
        assert meta["test_command"] == ""

        content = (Path(tmp_workspace) / "AI-TASKS.md").read_text(encoding="utf-8")
        assert "测试命令" not in content

    def test_done_task_with_timestamp_format(self, tmp_workspace):
        """已完成任务带时间戳格式 (Round N, YYYY-MM-DD HH:MM) 正确解析。"""
        content = textwrap.dedent("""\
            # AI 任务列表
            生成时间: 2026-06-15 16:16:30
            运行次数: 2
            最后运行: 2026-06-15 17:30:00
            全局轮次: 5

            共 2 个任务

            ## 已完成
            - [x] **#1** 修复空指针 (Round 3, 2026-06-15 16:30)
        """)
        (Path(tmp_workspace) / "AI-TASKS.md").write_text(content, encoding="utf-8")
        loaded = ac.load_task_list(tmp_workspace)
        assert loaded is not None
        assert len(loaded) == 1
        assert loaded[0]["id"] == 1
        assert loaded[0]["status"] == "done"
        assert loaded[0]["completed_round"] == 3
        assert loaded[0]["completed_time"] == "2026-06-15 16:30"
        assert loaded[0]["title"] == "修复空指针"

    def test_done_task_old_format_without_timestamp(self, tmp_workspace):
        """旧格式已完成任务（无时间戳）仍然正确解析。"""
        content = textwrap.dedent("""\
            # AI 任务列表
            生成时间: 2026-01-01 10:00:00

            共 1 个任务

            ## 已完成
            - [x] **#5** 旧格式任务 (Round 2)
        """)
        (Path(tmp_workspace) / "AI-TASKS.md").write_text(content, encoding="utf-8")
        loaded = ac.load_task_list(tmp_workspace)
        assert loaded is not None
        assert len(loaded) == 1
        assert loaded[0]["id"] == 5
        assert loaded[0]["status"] == "done"
        assert loaded[0]["completed_round"] == 2
        assert loaded[0]["completed_time"] == ""

    def test_backup_task_file(self, tmp_workspace, sample_tasks):
        """backup_task_file 创建带日期时间的 .bak 备份。"""
        ac.save_task_list(tmp_workspace, sample_tasks)
        ac.backup_task_file(tmp_workspace)

        # 查找匹配 AI-TASKS.md.YYYYMMDD_HHMMSS.bak 模式的文件
        bak_files = list(Path(tmp_workspace).glob("AI-TASKS.md.*.bak"))
        assert len(bak_files) == 1
        bak_path = bak_files[0]
        # 原文件仍然存在
        assert (Path(tmp_workspace) / ac.TASK_FILE).is_file()
        # bak 内容应与原文件一致
        original = (Path(tmp_workspace) / ac.TASK_FILE).read_text(encoding="utf-8")
        backup = bak_path.read_text(encoding="utf-8")
        assert original == backup

    def test_backup_task_file_no_source(self, tmp_workspace):
        """无原文件时 backup_task_file 不报错也不创建 bak。"""
        ac.backup_task_file(tmp_workspace)
        bak_files = list(Path(tmp_workspace).glob("AI-TASKS.md.*.bak"))
        assert len(bak_files) == 0

    def test_save_done_task_includes_timestamp(self, tmp_workspace, sample_tasks):
        """保存已完成任务时包含 completed_time 字段。"""
        # 模拟 mark_task_done 的效果
        for t in sample_tasks:
            if t["id"] == 3:
                t["completed_time"] = "2026-06-15 16:30"
        ac.save_task_list(tmp_workspace, sample_tasks)

        content = (Path(tmp_workspace) / "AI-TASKS.md").read_text(encoding="utf-8")
        # 已完成任务应有时间戳
        assert "(Round 5, 2026-06-15 16:30)" in content


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
        assert "Current Task" in result

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
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired(
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

    def test_git_status_with_quoted_paths(self, tmp_workspace):
        """git status --porcelain 中含引号包裹的路径名（空格路径）。"""
        git_dir = Path(tmp_workspace) / ".git"
        git_dir.mkdir()

        mock_result = MagicMock()
        mock_result.stdout = (
            ' M "src/my file.py"\n'
            '?? "docs/read me.md"\n'
        )

        with patch("subprocess.run", return_value=mock_result):
            files = ac.get_changed_files(tmp_workspace)
            assert "src/my file.py" in files
            assert "docs/read me.md" in files
            # 路径不应残留引号
            assert '"src/my file.py"' not in files

    def test_git_status_with_quoted_rename(self, tmp_workspace):
        """重命名且两个路径都含空格（均被引号包裹）。"""
        git_dir = Path(tmp_workspace) / ".git"
        git_dir.mkdir()

        mock_result = MagicMock()
        mock_result.stdout = 'R  "old name.py" -> "new name.py"\n'

        with patch("subprocess.run", return_value=mock_result):
            files = ac.get_changed_files(tmp_workspace)
            assert "new name.py" in files
            # 旧文件名不应出现
            assert "old name.py" not in files
            # 不应残留引号
            assert '"new name.py"' not in files

    def test_git_status_with_rename(self, tmp_workspace):
        """git status 中重命名文件处理。"""
        git_dir = Path(tmp_workspace) / ".git"
        git_dir.mkdir()

        mock_result = MagicMock()
        mock_result.stdout = "R  old_name.py -> new_name.py\n"

        with patch("subprocess.run", return_value=mock_result):
            files = ac.get_changed_files(tmp_workspace)
            assert "new_name.py" in files

    def test_git_status_with_unquoted_rename_quoted_target(self, tmp_workspace):
        """重命名时仅目标路径含空格被引号包裹。"""
        git_dir = Path(tmp_workspace) / ".git"
        git_dir.mkdir()

        mock_result = MagicMock()
        mock_result.stdout = 'R  old.py -> "new file.py"\n'

        with patch("subprocess.run", return_value=mock_result):
            files = ac.get_changed_files(tmp_workspace)
            assert "new file.py" in files
            assert '"new file.py"' not in files

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
# init_log / write_round_log / write_run_header
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
        assert "无(本轮无代码变更)" in content

    def test_write_run_header(self, tmp_workspace):
        """write_run_header 在 changelog 中写入运行头部。"""
        ac.init_log(tmp_workspace, "pi")
        ac.write_run_header(tmp_workspace, 3)
        content = (Path(tmp_workspace) / "AI-CHANGELOG.md").read_text(encoding="utf-8")
        assert "运行 #3" in content


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
timeout = 300
sleep = 1.5
no_backup = true
agent_args = "--model gpt-4"
keep_backups = 10
plan_only = false
replan = false
dry_run = false
"""
        cfg_path = Path(tmp_workspace) / ".ai-controller.toml"
        cfg_path.write_text(toml_content, encoding="utf-8")
        config = ac.load_config(tmp_workspace)

        assert config["agent"] == "claude"
        assert config["max_rounds"] == 5
        assert config["timeout"] == 300
        assert config["sleep"] == 1.5
        assert config["no_backup"] is True
        assert config["agent_args"] == "--model gpt-4"
        assert config["keep_backups"] == 10
        assert config["plan_only"] is False
        assert config["replan"] is False
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
timeout: 120
sleep: 3.0
no_backup: true
agent_args: "-m claude-sonnet-4"
keep_backups: 5
plan_only: false
replan: true
dry_run: true
"""
        # 需要 PyYAML
        pytest.importorskip("yaml")
        cfg_path = Path(tmp_workspace) / ".ai-controller.yaml"
        cfg_path.write_text(yaml_content, encoding="utf-8")
        config = ac.load_config(tmp_workspace)

        assert config["agent"] == "claude"
        assert config["max_rounds"] == 3
        assert config["timeout"] == 120
        assert config["sleep"] == 3.0
        assert config["no_backup"] is True
        assert config["agent_args"] == "-m claude-sonnet-4"
        assert config["keep_backups"] == 5
        assert config["plan_only"] is False
        assert config["replan"] is True
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
        """配置文件中的未知键被忽略（包括已移除的 resume）。"""
        toml_content = """\
agent = "pi"
resume = true
unknown_param = "should be ignored"
"""
        cfg_path = Path(tmp_workspace) / ".ai-controller.toml"
        cfg_path.write_text(toml_content, encoding="utf-8")
        config = ac.load_config(tmp_workspace)

        assert config["agent"] == "pi"
        assert "resume" not in config  # 已移除
        assert "unknown_param" not in config
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


# ═══════════════════════════════════════════════════════════════════════
# consecutive_noops 跨任务隔离
# ═══════════════════════════════════════════════════════════════════════

class TestConsecutiveNoopsIsolation:
    """验证 consecutive_noops 按任务独立计数，不会跨任务泄漏。

    场景: 任务 #1 连续失败 2 次(consecutive_noops=2)，然后任务 #1
    因外部原因被标记完成，任务 #2 开始执行。任务 #2 的首次失败不应
    触发跳过逻辑 —— 每个任务应有独立的重试计数。
    """

    def test_noops_reset_on_task_switch(self, tmp_workspace, sample_tasks):
        """任务 ID 变化时 consecutive_noops 应重置为 0。"""
        import ai_controller.cli as cli_module

        # 仅保留 id=1 和 id=2 两个 pending 任务
        tasks = [t for t in sample_tasks if t["id"] in (1, 2)]
        for t in tasks:
            t["status"] = "pending"
        task1 = tasks[0]
        task2 = tasks[1]

        # 预创建任务文件，避免 run_loop 触发 generate_task_list
        ac.save_task_list(tmp_workspace, tasks)

        # 模拟 get_next_pending_task 的返回序列:
        #   调用 1-2: 任务 #1 (模拟连续失败 2 次)
        #   调用 3-6: 任务 #2 (模拟任务 #1 被外部标记完成后的新任务)
        #     任务 #2 失败 3 次后 consecutive_noops=3,
        #     第 4 次取任务时触发跳过逻辑
        #   调用 7: None (全部完成)
        call_count = [0]
        task_sequence = [task1, task1, task2, task2, task2, task2, None]

        def mock_get_next_pending_task(target_dir, tasks_list=None):
            idx = call_count[0]
            call_count[0] += 1
            if idx >= len(task_sequence):
                return None
            return task_sequence[idx]

        # 记录 mark_task_done 被调用的任务 ID
        marked_done_ids = []

        def mock_mark_task_done(target_dir, task_id, round_num, tasks_list,
                                run_count=None, last_run=None, global_round=None,
                                gen_time=None):
            marked_done_ids.append(task_id)
            # 更新任务状态，模拟真实行为
            ac.mark_task_done(target_dir, task_id, round_num, tasks_list,
                              run_count=run_count, last_run=last_run,
                              global_round=global_round, gen_time=gen_time)

        # _execute_single_round 始终返回失败+无改动
        noop_result = {
            "success": False, "summary": "noop", "changed_files": [],
            "elapsed": 1.0, "has_diff": False,
        }

        with patch.object(cli_module, "write_run_header"):
            with patch.object(cli_module, "init_log"):
                with patch.object(cli_module, "save_task_list"):
                    with patch.object(cli_module, "_execute_single_round",
                                      return_value=noop_result):
                        with patch.object(cli_module, "is_git_repo",
                                          return_value=False):
                            with patch.object(
                                cli_module, "get_next_pending_task",
                                side_effect=mock_get_next_pending_task,
                                autospec=True,
                            ):
                                with patch.object(
                                    cli_module, "mark_task_done",
                                    side_effect=mock_mark_task_done,
                                    autospec=True,
                                ):
                                    cli_module.run_loop(
                                        target_dir=tmp_workspace,
                                        agent="pi",
                                        max_rounds=10,
                                        no_backup=True,
                                        sleep_between=0,
                                        timeout=10,
                                    )

        # 验证: 任务 #2 被完整重试了 3 次后跳过,
        # 而非交叉污染导致仅 1 次失败就被跳过
        # 预期: 任务 #1 失败 2 次(未达阈值), 任务 #2 失败 3 次后跳过
        # mark_task_done 应只对任务 #2 调用(因为任务 #1 未被标记,
        # 只通过 continue 重试)
        assert marked_done_ids == [2], (
            f"只应跳过任务 #2(连续 3 次失败后), 实际标记: {marked_done_ids}"
        )


# ═══════════════════════════════════════════════════════════════════════
# validation — run_test_command
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════
# git_stash_push / git_stash_pop
# ═══════════════════════════════════════════════════════════════════════

class TestGitStashPushPop:
    """测试 git stash push/pop 函数（通过 mock subprocess）。"""

    def test_stash_push_success(self):
        """正常 stash push。"""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            result = ac.git_stash_push("/tmp/test")
            assert result is True

    def test_stash_push_no_changes(self):
        """无改动时 stash push 返回 False。"""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = "No local changes to save\n"

        with patch("subprocess.run", return_value=mock_proc):
            result = ac.git_stash_push("/tmp/test")
            assert result is False

    def test_stash_push_failure(self):
        """stash push 失败返回 False。"""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "error: failed to stash"

        with patch("subprocess.run", return_value=mock_proc):
            result = ac.git_stash_push("/tmp/test")
            assert result is False

    def test_stash_push_exception(self):
        """subprocess 异常时 stash push 返回 False。"""
        with patch("subprocess.run", side_effect=OSError("git not found")):
            result = ac.git_stash_push("/tmp/test")
            assert result is False

    def test_stash_pop_success(self):
        """正常 stash pop。"""
        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with patch("subprocess.run", return_value=mock_proc):
            result = ac.git_stash_pop("/tmp/test")
            assert result is True

    def test_stash_pop_conflict(self):
        """stash pop 冲突返回 False。"""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "CONFLICT in file"

        with patch("subprocess.run", return_value=mock_proc):
            result = ac.git_stash_pop("/tmp/test")
            assert result is False

    def test_stash_pop_exception(self):
        """subprocess 异常时 stash pop 返回 False。"""
        with patch("subprocess.run", side_effect=OSError("git not found")):
            result = ac.git_stash_pop("/tmp/test")
            assert result is False


# ═══════════════════════════════════════════════════════════════════════
# ensure_gitignore
# ═══════════════════════════════════════════════════════════════════════

class TestEnsureGitignore:
    """测试 ensure_gitignore 自动管理目标仓库 .gitignore。"""

    def test_no_gitignore_file(self, tmp_workspace):
        """目标目录无 .gitignore 文件且非 git 仓库，跳过，返回 False。"""
        result = ac.ensure_gitignore(tmp_workspace)
        assert result is False

    def test_no_gitignore_but_git_repo_creates_it(self, tmp_workspace):
        """git 仓库中无 .gitignore 时，自动创建并写入控制器条目。"""
        (Path(tmp_workspace) / ".git").mkdir()
        result = ac.ensure_gitignore(tmp_workspace)
        assert result is True
        content = (Path(tmp_workspace) / ".gitignore").read_text(encoding="utf-8")
        assert "# AI 自迭代控制器 生成文件" in content
        assert "AI-TASKS.md" in content
        assert "AI-CHANGELOG.md" in content
        assert "ai-controller.log" in content
        assert ".ai-controller-backups/" in content

    def test_all_entries_already_present(self, tmp_workspace):
        ".gitignore 已包含所有生成路径，不修改，返回 False。"""
        content = (
            "# Python\n__pycache__/\n"
            "# AI 自迭代控制器 生成文件\n"
            "AI-TASKS.md\n"
            "AI-CHANGELOG.md\n"
            "ai-controller.log\n"
            ".ai-controller-backups/\n"
        )
        (Path(tmp_workspace) / ".gitignore").write_text(content, encoding="utf-8")
        result = ac.ensure_gitignore(tmp_workspace)
        assert result is False

        # 内容不变
        after = (Path(tmp_workspace) / ".gitignore").read_text(encoding="utf-8")
        assert after == content

    def test_missing_all_entries(self, tmp_workspace):
        ".gitignore 不含任何生成路径，追加全部，返回 True。"""
        (Path(tmp_workspace) / ".gitignore").write_text(
            "__pycache__/\n.venv/\n", encoding="utf-8"
        )
        result = ac.ensure_gitignore(tmp_workspace)
        assert result is True

        content = (Path(tmp_workspace) / ".gitignore").read_text(encoding="utf-8")
        assert "# AI 自迭代控制器 生成文件" in content
        assert "AI-TASKS.md" in content
        assert "AI-CHANGELOG.md" in content
        assert "ai-controller.log" in content
        assert ".ai-controller-backups/" in content
        # 原有内容仍在
        assert "__pycache__/" in content
        assert ".venv/" in content

    def test_missing_some_entries(self, tmp_workspace):
        """部分路径已存在，只追加缺失的。"""
        (Path(tmp_workspace) / ".gitignore").write_text(
            "AI-CHANGELOG.md\nai-controller.log\n", encoding="utf-8"
        )
        result = ac.ensure_gitignore(tmp_workspace)
        assert result is True

        content = (Path(tmp_workspace) / ".gitignore").read_text(encoding="utf-8")
        # 缺失的项已追加
        assert "AI-TASKS.md" in content
        assert ".ai-controller-backups/" in content
        # 已有的项未重复
        lines = content.splitlines()
        ai_changelog_lines = [l for l in lines if l.strip() == "AI-CHANGELOG.md"]
        assert len(ai_changelog_lines) == 1

    def test_entries_with_spaces_in_gitignore(self, tmp_workspace):
        ".gitignore 行带前后空格，匹配仍正常工作。"""
        (Path(tmp_workspace) / ".gitignore").write_text(
            "  AI-TASKS.md\n  AI-CHANGELOG.md  \n", encoding="utf-8"
        )
        result = ac.ensure_gitignore(tmp_workspace)
        assert result is True
        content = (Path(tmp_workspace) / ".gitignore").read_text(encoding="utf-8")
        assert "ai-controller.log" in content
        assert ".ai-controller-backups/" in content

    def test_empty_gitignore(self, tmp_workspace):
        """空 .gitignore 追加全部条目。"""
        (Path(tmp_workspace) / ".gitignore").write_text("", encoding="utf-8")
        result = ac.ensure_gitignore(tmp_workspace)
        assert result is True

        content = (Path(tmp_workspace) / ".gitignore").read_text(encoding="utf-8")
        assert "# AI 自迭代控制器 生成文件" in content
        assert "AI-TASKS.md" in content

    def test_comment_lines_not_confused_as_entries(self, tmp_workspace):
        """注释行 #AI-TASKS.md 不应被误判为匹配。"""
        (Path(tmp_workspace) / ".gitignore").write_text(
            "#AI-TASKS.md is something else\n", encoding="utf-8"
        )
        result = ac.ensure_gitignore(tmp_workspace)
        assert result is True  # 仍需追加真正的条目

    def test_existing_content_preserved(self, tmp_workspace):
        ".gitignore 原有内容和顺序不受影响。"""
        original_text = "# Python\n__pycache__/\n*.pyc\n"
        (Path(tmp_workspace) / ".gitignore").write_text(original_text, encoding="utf-8")
        ac.ensure_gitignore(tmp_workspace)

        content = (Path(tmp_workspace) / ".gitignore").read_text(encoding="utf-8")
        assert content.startswith(original_text.rstrip("\n"))

    def test_idempotent(self, tmp_workspace):
        """多次调用不会重复追加。"""
        (Path(tmp_workspace) / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        ac.ensure_gitignore(tmp_workspace)
        ac.ensure_gitignore(tmp_workspace)
        ac.ensure_gitignore(tmp_workspace)

        content = (Path(tmp_workspace) / ".gitignore").read_text(encoding="utf-8")
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        # 每个条目只出现一次
        assert lines.count("AI-TASKS.md") == 1
        assert lines.count("AI-CHANGELOG.md") == 1
        assert lines.count("ai-controller.log") == 1
        assert lines.count(".ai-controller-backups/") == 1

# ═══════════════════════════════════════════════════════════════════════

class TestExtractJsonTasksExtra:
    """覆盖新修复的场景。"""

    def test_braces_in_description_string(self):
        """description 中包含花括号 {user} {count}（字符串内）。"""
        text = '先扫描代码库...\n{"tasks": [{"id": 1, "description": "修改 {user} 变量"}], "summary": "ok"}'
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks is not None
        assert tasks[0]["description"] == "修改 {user} 变量"
        assert tc is None

    def test_tilde_code_block(self):
        """~~~ 代码块。"""
        text = '结果：\n~~~json\n{"tasks": [{"id": 1, "title": "修复"}]}\n~~~'
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks == [{"id": 1, "title": "修复"}]
        assert tc is None

    def test_no_newline_before_closing_fence(self):
        """代码块无尾部换行就闭合。"""
        text = '```json\n{"tasks": [{"id": 1}]}```'
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks == [{"id": 1}]
        assert tc is None

    def test_no_newline_before_or_after_fence(self):
        """代码块无换行。"""
        text = '```{"tasks": [{"id": 1}]}```'
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks == [{"id": 1}]
        assert tc is None

    def test_deeply_nested_json(self):
        """深层嵌套的 JSON。"""
        text = '{"tasks": [{"id": 1, "meta": {"level": {"value": 3}}}], "summary": "ok"}'
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks == [{"id": 1, "meta": {"level": {"value": 3}}}]
        assert tc is None

    def test_escaped_backslash_in_string(self):
        """字符串中的转义反斜杠。"""
        text = '{"tasks": [{"id": 1, "path": "C:\\\\dir\\\\file"}], "summary": "ok"}'
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks == [{"id": 1, "path": "C:\\dir\\file"}]
        assert tc is None

    def test_mixed_code_block_variants(self):
        """多种代码块形式在同一个文本中（取第一个有效的）。"""
        text = '```\n{"tasks": [{"id": 1}]}\n``` 然后 ~~~\n{"tasks": [{"id": 2}]}\n~~~'
        tasks, tc = ac._extract_json_tasks(text)
        # 应匹配第一个 ``` 代码块
        assert tasks == [{"id": 1}]
        assert tc is None

    def test_tasks_array_empty(self):
        """空 tasks 数组。"""
        text = '{"tasks": [], "summary": "无任务"}'
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks == []
        assert tc is None

    def test_no_tasks_key_only_other_data(self):
        """JSON 有内容但无 tasks 键。"""
        text = '{"summary": "仅总结", "version": 2}'
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks is None
        assert tc is None

    def test_json_with_trailing_text(self):
        """JSON 后有额外文字。"""
        text = '{"tasks": [{"id": 1}], "summary": "ok"} 以上是任务列表。'
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks == [{"id": 1}]
        assert tc is None

    def test_preamble_with_angle_braces(self):
        """前置文本中有尖括号（非花括号，确保不受影响）。"""
        text = '<project> <name> 分析结果：{"tasks": [{"id": 1}], "summary": "ok"}'
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks == [{"id": 1}]
        assert tc is None

    def test_lone_close_brace_in_description(self):
        """description 字符串内包含孤立 }（字符串感知栈匹配的关键场景）。"""
        text = '{"tasks": [{"id": 1, "description": "替换 } 符号为 \\"}code", "title": "修复"}], "summary": "ok"}'
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks is not None
        assert tasks[0]["description"] == '替换 } 符号为 "}code'
        assert tc is None

    def test_all_braces_in_strings_only(self):
        """所有花括号出现在字符串值中，无实际嵌套结构。"""
        text = '{"tasks": [{"id": 1, "title": "处理 {placeholder} 和 } 符号", "desc": "a{b}c"}], "summary": "ok"}'
        tasks, tc = ac._extract_json_tasks(text)
        assert tasks is not None
        assert tasks[0]["title"] == '处理 {placeholder} 和 } 符号'
        assert tc is None


# ═══════════════════════════════════════════════════════════════════════
# build_retry_prompt
# ═══════════════════════════════════════════════════════════════════════

class TestBuildRetryPrompt:
    """测试构建测试失败后的修复 prompt。"""

    def test_basic_retry_prompt(self):
        task = {
            "type": "bug fix",
            "title": "修复空指针",
            "description": "在 foo.py 加入 null 检查",
        }
        result = ac.build_retry_prompt(
            task,
            test_command="pytest tests/",
            test_output="FAILED test_foo.py::test_bar - AssertionError",
            changed_files=["src/foo.py", "tests/test_foo.py"],
        )
        assert "测试失败，需要修复" in result
        assert "在 foo.py 加入 null 检查" in result
        assert "pytest tests/" in result
        assert "FAILED test_foo.py" in result
        assert "src/foo.py" in result

    def test_retry_prompt_no_files(self):
        task = {"title": "某个任务", "description": "修改代码"}
        result = ac.build_retry_prompt(
            task,
            test_command="make test",
            test_output="error",
            changed_files=[],
        )
        assert "(无文件改动)" in result

    def test_retry_prompt_escapes_braces(self):
        """task description 中的花括号应被正确转义。"""
        task = {
            "title": "test",
            "description": "修改 {variable} 的值",
        }
        result = ac.build_retry_prompt(
            task,
            test_command="pytest",
            test_output="fail",
            changed_files=["a.py"],
        )
        # 不应抛出 KeyError
        assert "修改 {variable} 的值" in result


# ═══════════════════════════════════════════════════════════════════════
# run_test_command
# ═══════════════════════════════════════════════════════════════════════

class TestRunTestCommand:
    """测试 run_test_command 函数。"""

    def test_command_passes(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "All tests passed"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            passed, output = ac.run_test_command(
                "pytest", "/tmp/test", 60,
            )
            assert passed is True
            assert "All tests passed" in output

    def test_command_fails(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "FAIL: test failed"

        with patch("subprocess.run", return_value=mock_proc):
            passed, output = ac.run_test_command(
                "pytest", "/tmp/test", 60,
            )
            assert passed is False
            assert "FAIL: test failed" in output

    def test_command_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(
            cmd="pytest", timeout=60,
        )):
            passed, output = ac.run_test_command(
                "pytest", "/tmp/test", 60,
            )
            assert passed is False
            assert "超时" in output

    def test_command_exception(self):
        with patch("subprocess.run", side_effect=OSError("not found")):
            passed, output = ac.run_test_command(
                "pytest", "/tmp/test", 60,
            )
            assert passed is False
            assert "异常" in output

    def test_command_uses_shell_and_cwd(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "ok"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            ac.run_test_command("pytest -v", "/some/project", 120)
            kwargs = mock_run.call_args.kwargs
            assert kwargs["shell"] is True
            assert kwargs["cwd"] == "/some/project"
            assert kwargs["timeout"] == 120


# ═══════════════════════════════════════════════════════════════════════
# _execute_task_with_retry
# ═══════════════════════════════════════════════════════════════════════

class TestExecuteTaskWithRetry:
    """测试带重试的任务执行逻辑（通过 mock）。"""

    def test_no_test_command_returns_directly(self, tmp_workspace):
        """无 test_command 时直接返回 agent 执行结果。"""
        task = {"id": 1, "title": "test", "description": "do something"}
        task_prompt = ac.build_task_prompt(task)

        mock_result = {
            "success": True, "summary": "done",
            "changed_files": ["a.py"], "elapsed": 1.0,
            "has_diff": True,
        }

        with patch.object(ac.cli, "_execute_single_round", return_value=mock_result):
            result = ac.cli._execute_task_with_retry(
                target_dir=tmp_workspace,
                agent="pi",
                task=task,
                task_prompt=task_prompt,
                test_command=None,
                max_retries=3,
                no_backup=True,
                timeout=10,
                agent_args=None,
                keep_backups=0,
                round_num=1,
                sleep_between=0,
            )
        assert result["success"] is True
        assert result["retries_used"] == 0
        assert result["final_test_passed"] is None

    def test_test_passes_on_first_try(self, tmp_workspace):
        """测试在第一次尝试就通过。"""
        task = {"id": 2, "title": "fix", "description": "fix"}
        task_prompt = ac.build_task_prompt(task)

        mock_result = {
            "success": True, "summary": "fixed",
            "changed_files": ["b.py"], "elapsed": 0.5,
            "has_diff": True,
        }

        with patch.object(ac.cli, "_execute_single_round", return_value=mock_result):
            with patch.object(ac.cli, "run_test_command", return_value=(True, "ok")):
                result = ac.cli._execute_task_with_retry(
                    target_dir=tmp_workspace,
                    agent="pi",
                    task=task,
                    task_prompt=task_prompt,
                    test_command="pytest",
                    max_retries=3,
                    no_backup=True,
                    timeout=10,
                    agent_args=None,
                    keep_backups=0,
                    round_num=1,
                    sleep_between=0,
                )
        assert result["success"] is True
        assert result["retries_used"] == 0
        assert result["final_test_passed"] is True

    def test_retry_and_eventually_pass(self, tmp_workspace):
        """第一次测试失败，重试后通过。"""
        task = {"id": 3, "title": "fix", "description": "fix"}
        task_prompt = ac.build_task_prompt(task)

        # 第一次 agent 调用成功但有文件改动
        # 测试第一次失败，第二次通过
        call_count = [0]

        def mock_single_round(*args, **kwargs):
            call_count[0] += 1
            return {
                "success": True, "summary": f"attempt {call_count[0]}",
                "changed_files": ["c.py"], "elapsed": 0.3,
                "has_diff": True,
            }

        test_results = [(False, "FAIL"), (True, "PASS")]
        test_call_count = [0]

        def mock_test(*args, **kwargs):
            idx = test_call_count[0]
            test_call_count[0] += 1
            return test_results[min(idx, len(test_results) - 1)]

        with patch.object(ac.cli, "_execute_single_round", side_effect=mock_single_round):
            with patch.object(ac.cli, "run_test_command", side_effect=mock_test):
                with patch("time.sleep"):  # 避免实际等待
                    result = ac.cli._execute_task_with_retry(
                        target_dir=tmp_workspace,
                        agent="pi",
                        task=task,
                        task_prompt=task_prompt,
                        test_command="pytest",
                        max_retries=3,
                        no_backup=True,
                        timeout=10,
                        agent_args=None,
                        keep_backups=0,
                        round_num=1,
                        sleep_between=0,
                    )
        # 第一次尝试 + 第一次重试 = 2 次 agent 调用
        assert call_count[0] == 2
        assert result["retries_used"] == 1
        assert result["final_test_passed"] is True

    def test_all_retries_exhausted(self, tmp_workspace):
        """所有重试都用完仍未通过测试。"""
        task = {"id": 4, "title": "broken", "description": "unfixable"}
        task_prompt = ac.build_task_prompt(task)

        mock_result = {
            "success": True, "summary": "try",
            "changed_files": ["d.py"], "elapsed": 0.2,
            "has_diff": True,
        }

        with patch.object(ac.cli, "_execute_single_round", return_value=mock_result):
            with patch.object(ac.cli, "run_test_command", return_value=(False, "STILL FAIL")):
                with patch("time.sleep"):
                    result = ac.cli._execute_task_with_retry(
                        target_dir=tmp_workspace,
                        agent="pi",
                        task=task,
                        task_prompt=task_prompt,
                        test_command="pytest",
                        max_retries=2,
                        no_backup=True,
                        timeout=10,
                        agent_args=None,
                        keep_backups=0,
                        round_num=1,
                        sleep_between=0,
                    )
        assert result["retries_used"] == 2
        assert result["final_test_passed"] is False

    def test_no_diff_skips_test(self, tmp_workspace):
        """本轮无文件改动时跳过测试。"""
        task = {"id": 5, "title": "nop", "description": "nothing"}
        task_prompt = ac.build_task_prompt(task)

        mock_result = {
            "success": True, "summary": "no changes",
            "changed_files": [], "elapsed": 1.0,
            "has_diff": False,
        }

        test_called = [False]
        def mock_test(*args, **kwargs):
            test_called[0] = True
            return (True, "ok")

        with patch.object(ac.cli, "_execute_single_round", return_value=mock_result):
            with patch.object(ac.cli, "run_test_command", side_effect=mock_test):
                result = ac.cli._execute_task_with_retry(
                    target_dir=tmp_workspace,
                    agent="pi",
                    task=task,
                    task_prompt=task_prompt,
                    test_command="pytest",
                    max_retries=3,
                    no_backup=True,
                    timeout=10,
                    agent_args=None,
                    keep_backups=0,
                    round_num=1,
                    sleep_between=0,
                )
        assert test_called[0] is False  # 测试不应被调用
        assert result["final_test_passed"] is None


# ═══════════════════════════════════════════════════════════════════════
# _truncate_test_output
# ═══════════════════════════════════════════════════════════════════════

from ai_controller.prompts import _truncate_test_output  # noqa: E402


class TestTruncateTestOutput:
    """测试 _truncate_test_output 截断逻辑。

    将 prompts.py __main__ 自检中的 4 个断言迁移为 pytest 用例，
    并增加边界条件覆盖。
    """

    def test_short_output_unchanged(self):
        """短输出不触发任何截断，原样返回。"""
        short = "line1\nline2"
        assert _truncate_test_output(short) == short

    def test_many_lines_truncated(self):
        """超出行数限制时，仅保留尾部 max_lines 行并附加截断提示。"""
        many_lines = "\n".join(f"line{i}" for i in range(100))
        result = _truncate_test_output(many_lines, max_lines=50, max_chars=10000)
        assert "[... 输出已截断" in result
        assert result.count("\n") <= 51

    def test_long_content_truncated(self):
        """超出字符数限制时，仅保留尾部 max_chars 个字符并附加截断提示。"""
        long_content = "x" * 5000
        result = _truncate_test_output(long_content, max_lines=50, max_chars=4000)
        assert "[... 输出已截断" in result
        assert len(result) <= 4000 + 200

    def test_empty_string(self):
        """空字符串输入输出均为空字符串。"""
        assert _truncate_test_output("") == ""

    def test_single_line_exceeds_max_chars(self):
        """单行输出超过 max_chars 时，字符级截断生效。"""
        single_line = "A" * 500
        result = _truncate_test_output(single_line, max_lines=50, max_chars=200)
        assert "[... 输出已截断" in result
        # 截断后纯内容不超过 max_chars，加上截断提示后长度在合理范围
        content_after_note = result.split("\n\n", 1)[-1]
        assert len(content_after_note) == 200

    def test_truncation_exactly_at_max_chars_boundary(self):
        """输出长度恰好等于 max_chars 时，不触发字符截断（测试 off-by-one）。"""
        # 5 行，每行 10 个字符：5*10 + 4 个换行符 = 54
        content = "\n".join("x" * 10 for _ in range(5))
        assert len(content) == 54
        result = _truncate_test_output(content, max_lines=10, max_chars=54)
        assert result == content

    def test_both_constraints_apply_lines_then_chars(self):
        """行数和字符数同时超限时，先截行再截字符。"""
        # 60 行，每行 100 个字符：60*100 + 59 = 6059 字符
        content = "\n".join("y" * 100 for _ in range(60))
        result = _truncate_test_output(content, max_lines=30, max_chars=1500)
        assert "[... 输出已截断" in result
        # 行截断后 30*100+29 = 3029 > 1500，字符截断也会触发
        content_after_note = result.split("\n\n", 1)[-1]
        assert len(content_after_note) == 1500


