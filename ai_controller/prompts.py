"""提示词模板 —— 规划提示词与任务执行提示词。"""

import textwrap

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


def build_task_prompt(task: dict) -> str:
    """为单个任务构建执行提示词"""
    desc = f"**[{task.get('type', '改进')}] {task.get('title', '')}**\n\n{task.get('description', '')}"
    return TASK_PROMPT.format(task_description=desc)
