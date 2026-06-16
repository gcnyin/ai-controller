"""AI 自迭代控制器 —— 模块化包结构。

模块划分：
    config     - 配置文件读取（load_config）
    prompts    - 提示词模板（PLAN_PROMPT, TASK_PROMPT, build_task_prompt）
    agent      - Agent 调用（AGENTS 配置, call_agent, parse_summary）
    tasks      - 任务列表管理（生成/加载/保存/标记）
    backup     - 备份管理（backup_all, cleanup_old_backups）
    git_ops    - Git 操作（is_git_repo, has_changes, git_commit 等）
    validation - 质量验证与回滚（run_py_compile, run_pytest, run_test_command, rollback_and_record）
    test_detector - 测试命令自动发现（detect_test_command）
    cli        - CLI 入口与主循环（main, run_loop 等）

向后兼容：所有公开 API 及测试所需的私有函数均在包级别重新导出。
"""

import subprocess  # 测试需要 ac.subprocess.TimeoutExpired


# ── 全局常量 ──

LOG_FILE = "AI-CHANGELOG.md"
LOGGER_FILE = "ai-controller.log"

# 备份/遍历时需要跳过的目录，backup.py 和 git_ops.py 共用
SKIP_DIRS = (".ai-controller-backups", ".git", "__pycache__", ".venv", "venv",
             "node_modules", "dist", "build", ".next")


# ── 子模块重导出 ──

from .prompts import PLAN_PROMPT, TASK_PROMPT, build_task_prompt
from .agent import AGENTS, call_agent, parse_summary, build_agent_command
from .tasks import (
    TASK_FILE,
    TASK_FILE_BAK,
    generate_task_list,
    save_task_list,
    load_task_list,
    load_task_metadata,
    mark_task_done,
    get_next_pending_task,
    backup_task_file,
    _extract_json_tasks,
    _try_parse_json,
)
from .backup import BACKUP_DIR_NAME, backup_all, cleanup_old_backups
from .validation import (
    run_py_compile,
    run_pytest,
    run_test_command,
    has_tests,
    rollback_and_record,
)
from .test_detector import detect_test_command
from .git_ops import (
    is_git_repo,
    has_changes,
    git_commit,
    git_stash_push,
    git_stash_pop,
    get_changed_files,
    get_git_diff_summary,
)
from .config import load_config, CONFIG_FILE_NAMES
from .cli import (
    main,
    run_loop,
    build_ext_filter_arg,
    check_ext_filter,
    ensure_gitignore,
    extract_model_hint,
    init_log,
    write_run_header,
    write_round_log,
    _take_pre_snapshot,
)
