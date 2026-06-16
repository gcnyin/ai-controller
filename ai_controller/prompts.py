"""提示词模板 —— 规划提示词与任务执行提示词。"""

import textwrap

PLAN_PROMPT = textwrap.dedent("""\
    仔细看看这个项目，你觉得它还有哪些地方可以改进，或者可以新增什么功能？挑几个最值得做的列出来。

    聚焦真正有价值的事——这个改动能让用户多做什么、项目会因此变好多少。不要浪费时间在格式化、重命名、注释微调这些鸡毛蒜皮的事情上。

    ## 优先关注

    - 缺什么重要功能？有没有新特性能让项目更好用？
    - 有没有明显的 bug、崩溃、逻辑错误？
    - 哪些地方结构混乱、重复代码多，值得花时间重构？
    - 性能有没有可测量的瓶颈？
    - 关键路径有没有测试缺失？

    如果一件事做不做对项目没啥影响，直接不提。

    ## 输出格式

    用以下 JSON 格式输出（不要 markdown 代码块标记，纯 JSON）：

    {
      "tasks": [
        {"id": 1, "priority": "high", "type": "功能开发类", "title": "简短标题", "description": "要做什么、改哪个文件、为什么值得做"},
        {"id": 2, "priority": "medium", "type": "修复类", "title": "简短标题", "description": "..."}
      ],
      "summary": "整体评估总结（中文）"
    }

    规则：
    - id 从 1 开始递增，按优先级从高到低排列
    - priority 用 high / medium / low，优先列 high 和 medium
    - title 不超过 30 个字
    - description 说清楚改什么文件、做什么、为什么值得做
    - 如果项目已经很完善，tasks 可以给空数组
    - 禁止使用 emoji
    - 使用中文

    先扫描整个代码库，再给结论。
    """).strip()

TASK_PROMPT = textwrap.dedent("""\
    ## 当前任务

    {task_description}

    ## 行为准则

    - 直接修改文件，不要只给建议
    - 尽可能添加测试
    - 保持改动最小化，只做当前任务要求的事，不对无关部分动手
    - 确保改动后代码仍然可编译/可运行
    - 不改 .git/、node_modules/ 等非项目目录
    - 专注于做出有价值的实质改动。不要顺手调整代码风格、格式化、重命名等无关事项
    - 使用中文回复
    - 禁止使用 emoji

    改动完成后，在输出最后单独一行给出改动总结，格式：
    SUMMARY: <one-sentence English summary of what you changed>
    """).strip()


def build_task_prompt(task: dict) -> str:
    """为单个任务构建执行提示词"""
    desc = f"**[{task.get('type', '改进')}] {task.get('title', '')}**\n\n{task.get('description', '')}"
    return TASK_PROMPT.format(task_description=desc)
