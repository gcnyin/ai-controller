"""pytest 配置与共享 fixtures。

提供临时目录、测试数据等可复用的 fixtures。
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

# 将项目根目录添加到 sys.path，确保 ai_controller 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def tmp_workspace():
    """创建一个临时工作目录，在测试结束后自动清理。

    用于模拟目标项目目录，方便测试文件 I/O 相关函数。
    """
    tmpdir = tempfile.mkdtemp(prefix="ai_controller_test_")
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def sample_tasks():
    """提供一份示例任务列表，用于测试 save/load/mark 等函数。"""
    return [
        {
            "id": 1,
            "priority": "high",
            "type": "修复类",
            "title": "修复空指针异常",
            "description": "在 foo.py 的 bar 函数中增加 None 检查",
            "status": "pending",
        },
        {
            "id": 2,
            "priority": "medium",
            "type": "功能开发类",
            "title": "添加日志功能",
            "description": "在核心模块中加入结构化日志",
            "status": "pending",
        },
        {
            "id": 3,
            "priority": "low",
            "type": "性能优化类",
            "title": "优化数据库查询",
            "description": "给 user_query 添加索引",
            "status": "done",
            "completed_round": 5,
        },
    ]
