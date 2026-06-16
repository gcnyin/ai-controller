# AI 任务列表
生成时间: 2026-06-16 16:08:53
运行次数: 1
最后运行: 2026-06-16 16:08:53
全局轮次: 0

共 6 个任务

## 待执行

- [ ] **#1** [high] [功能开发类] Pre/Post 钩子：支持在每轮前后运行自定义命令
  新增 --pre-hook 和 --post-hook 参数，让用户在每次 agent 执行前/后运行脚本（如 pytest、linter、npm run build）。post-hook 失败时应能中止后续任务。修改 cli.py 的 _execute_single_round() 和 run_loop()，新增 hooks.py 模块封装子进程调用与超时处理。这是项目最关键的缺失能力——目前 agent 改完代码后无任何验证机制，用户无法信任自动迭代的结果，导致工具只能用于实验性项目而非真实工作流。

- [ ] **#2** [high] [修复类] 连续无改动时将任务标记为'跳过'而非'完成'
  当前 run_loop() 中 consecutive_noops >= 3 时调用 mark_task_done() 将任务写入'已完成'区域，但 agent 实际并未完成该任务，这会在统计和人工审查时造成误导。应新增 'skipped' 状态，修改 tasks.py 的 save_task_list()/load_task_list()/mark_task_done() 增加对 skipped 状态的支持，在 AI-TASKS.md 中新增'已跳过'区域并记录跳过原因。cli.py 第 392-398 行的 mark_task_done 调用应改为 mark_task_skipped。

- [ ] **#3** [high] [功能开发类] 按优先级或 ID 选择性执行任务
  新增 --priority 和 --tasks 参数。--priority high 只执行 high 优先级任务；--tasks 1,3,5 只执行指定 ID 的任务。修改 cli.py 的 run_loop() 在获取下一个待执行任务时增加过滤逻辑，tasks.py 的 get_next_pending_task() 增加 filter 参数。目前 AI 生成 20+ 条任务时，用户只能全量执行或手动编辑 AI-TASKS.md 文件来筛选，效率很低。这是使用频率最高的痛点之一。

- [ ] **#4** [medium] [功能开发类] 支持通过配置文件自定义 prompt 模板
  prompts.py 中 PLAN_PROMPT 和 TASK_PROMPT 硬编码，不同项目类型需要不同关注点（安全审计 vs 性能优化 vs 新功能开发）。在 .ai-controller.toml 中新增 [prompts] 节，允许覆盖 plan 和 task 模板，config.py 的 load_config() 和 known_params 需要相应扩展，cli.py 中将模板参数传递到 generate_task_list() 和 build_task_prompt()。这大幅提升工具的适用范围。

- [ ] **#5** [medium] [功能开发类] 拆分规划阶段与执行阶段的超时时间
  当前 --timeout 对规划阶段和每轮任务执行一视同仁。但规划阶段需要扫描全量代码库，通常耗时长得多。新增 --plan-timeout 参数（默认取 --timeout 的 2 倍或 1200 秒），修改 cli.py 的 main() 增加参数，run_loop() 中调用 generate_task_list() 时传入独立的 plan_timeout。避免规划阶段因超时而回退到逐轮模式。

- [ ] **#6** [medium] [重构类] run_loop() 拆分为独立的调度器类
  cli.py 的 run_loop() 约 150 行，混合了任务调度、模式切换、预览处理、日志输出等职责。应拆分为 TaskScheduler 类（负责任务队列调度、状态管理、退出条件判断），将 _run_legacy_loop 改为 LegacyScheduler，二者共用 _execute_single_round。这不会直接改变用户功能，但后续增加功能（钩子、过滤、并行执行）时不必在此函数中叠加更多 if-else，显著降低维护成本和回归风险。
