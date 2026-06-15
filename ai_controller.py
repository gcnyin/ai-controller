#!/usr/bin/env python3
"""
AI 自迭代控制器 —— 调用 pi/opencode/claude/codex 让 AI 自动循环改进代码。

默认两阶段模式：先让 AI 扫描代码库生成完整任务列表，再逐条执行。
检测到已有 AI-TASKS.md 时自动从未完成任务恢复。

用法:
    python ai_controller.py <目录> --agent pi [选项]

示例:
    # 默认模式：先规划再执行
    python ai_controller.py ./my-project --agent pi --max-rounds 10

    # 只生成任务列表 AI-TASKS.md
    python ai_controller.py ./my-project --agent pi --plan-only

    # 传统模式：每轮 AI 自行选择
    python ai_controller.py ./my-project --agent pi --max-rounds 10 --no-plan

    # 重新规划任务列表
    python ai_controller.py ./my-project --agent pi --replan
"""
from ai_controller.cli import main

if __name__ == "__main__":
    main()
