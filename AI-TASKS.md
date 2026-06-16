# AI 任务列表
生成时间: 2026-06-16 16:33:11
运行次数: 1
最后运行: 2026-06-16 16:25:30
全局轮次: 1

共 6 个任务

## 待执行

- [ ] **#2** [high] [修复类] _extract_json_tasks 首正则未启用 DOTALL，多行 JSON 匹配失败
  tasks.py 第73行，正则 r'```(?:json)?\s*\n(.*?)\n```' 中 (.*?) 未启用 re.DOTALL 标志，无法跨行匹配。当 AI 返回带格式化的多行 JSON 代码块（三行以上的 { ... }）时，第一个模式匹配失败，仅靠第二个 fallback 模式 r'\{[\s\S]*"tasks"[\s\S]*\}' 兜底。但 fallback 模式是裸 JSON 匹配，可能在输出中包含前导文本时匹配到错误位置。应在 re.search() 调用传入 re.DOTALL，或将 (.*?) 改为 ([\s\S]*?)。这直接影响规划阶段的任务列表解析成功率。

- [ ] **#3** [medium] [功能开发类] --dry-run --plan-only 仍调用 Agent 生成任务列表
  cli.py 第634-645行，--plan-only 分支直接调用 generate_task_list()，未检查 --dry-run 标志。同时设置 --dry-run --plan-only 时，该函数仍会启动 Agent 扫描代码库、消耗 API 配额。预览模式的语义是'不实际修改任何文件、不调用 Agent'，但规划阶段调用 Agent 就违背了这个语义。应在 plan-only 分支中判断 dry_run，若已有 AI-TASKS.md 则直接加载预览，若无则提示用户先运行不带 --dry-run 的 --plan-only。

- [ ] **#4** [medium] [功能开发类] 缺少 --start-from 参数：无法从指定任务ID恢复
  当前自动恢复逻辑总是从第一个待执行任务开始（tasks.py 第380行 get_next_pending_task 线性扫描）。如果用户手动完成了前5个任务，或者想跳过前几个不适合当前运行的任务，只能手动编辑 AI-TASKS.md 标记为完成。新增 --start-from <id> 参数，在 run_loop() 中跳过 id 小于指定值的任务，直接处理目标范围内的。这对 20+ 任务的大列表场景是高频需求。

- [ ] **#5** [medium] [功能开发类] 缺少 --context 参数：向 Agent 传递额外上下文
  prompts.py 中 PLAN_PROMPT 和 TASK_PROMPT 完全硬编码，用户无法对规划或执行阶段注入额外指令。例如用户想聚焦安全审计（'重点检查SQL注入和XSS'）或性能优化（'优先优化热路径'），目前只能靠手动编辑 AI-TASKS.md 的每条任务描述。新增 --context 或 --instruction 参数，在规划 prompt 和每条任务 prompt 尾部追加用户上下文，大幅提升工具的定向改进能力。比完全自定义模板（AI-TASKS #4）更轻量且覆盖80%的场景。

- [ ] **#6** [medium] [修复类] 缺少 --max-retries 每任务重试上限
  cli.py run_loop() 中，当一个任务 Agent 失败且无改动时，会无限重试同一任务，直到 consecutive_noops >= 3 全局阈值触发。没有针对单个任务的独立重试次数限制。一个困难任务理论上可以吃掉所有3次机会后才被跳过，而如果 consecutive_noops 是全局的（问题#1），还会连累后续任务。新增 --max-retries 参数（默认3），在 run_loop() 中每切换任务时重置计数器。加强调度的可预测性。

## 已完成

- [x] **#1** consecutive_noops 是跨任务共享全局计数器 (Round 1, 2026-06-16 16:33)
