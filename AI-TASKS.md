# AI 任务列表
生成时间: 2026-06-16 18:23:09
运行次数: 1
最后运行: 2026-06-16 18:18:46
全局轮次: 1

共 7 个任务

## 待执行

- [ ] **#2** [high] [修复类] git commit 会裹挟用户未提交改动
  run_loop() 中 has_changes() 只打印警告但继续执行，第一个 git_commit 用 git add -A 会把用户手头未提交的改动混入 AI 的 commit 中（含错误 commit message），可能导致用户丢失对自身改动的追踪。修复方案：检测到未提交改动时自动 git stash push -m 'ai-controller-auto-stash'，执行完后 git stash pop。改 cli.py 的 run_loop()。

- [ ] **#3** [high] [重构类] 拆分 cli.py 中的主循环逻辑
  cli.py 当前 500+ 行，承担了 CLI 参数解析、日志初始化、单轮执行(_execute_single_round)、主循环(run_loop)、传统循环(_run_legacy_loop)、预览模式(_dry_run_task_loop)等互相独立的多重职责。将 _execute_single_round、run_loop、_run_legacy_loop 提取到 ai_controller/loop.py，把日志函数(init_log, write_round_log, write_run_header)提取到 ai_controller/logger.py。这样每个模块职责清晰，也更容易加集成测试。

- [ ] **#4** [medium] [功能开发类] 可扩展的 Agent 注册机制
  目前 AGENTS 字典硬编码在 agent.py 中，用户想用其他 Agent（如 aider、continue.dev 等）必须修改源代码。改为支持通过配置文件 .ai-controller.toml 注册自定义 Agent：声明 cmd、args、cwd_option。示例：[[agents]] name='aider' cmd='aider' args=['--model', 'gpt-4', '--no-suggest-shell-commands']。改 agent.py 的 AGENTS 常量和 load_config 的 known_params。

- [ ] **#5** [medium] [功能开发类] 任务执行进度通知机制
  长时间运行(--max-rounds 0或大任务列表)时用户需要盯着终端才能知道进度。添加 --notify 参数支持三种通知方式：(1) desktop-notify（通过调用系统 notify-send/osascript），(2) webhook（POST JSON 到指定 URL），(3) 写入固定的 .ai-controller-status.json 文件供外部工具读取。这样用户可以离开终端，在任务完成或失败时得到主动通知。改 cli.py 的 run_loop()。

- [ ] **#6** [medium] [功能开发类] 并行执行独立任务
  当任务列表中有多个无文件依赖关系的独立任务时（如修复不同模块的不相关 bug），当前必须串行执行。如果能让这些任务并行（启动多个 Agent 进程分别修改不同文件），可以在多核机器上显著缩短总执行时间。需要做依赖分析（通过检查任务描述中的文件路径判断是否冲突）和冲突检测（git merge 级别）。价值高但实现复杂，改 cli.py 和 tasks.py。

- [ ] **#7** [low] [测试类] 补充核心编排路径的集成测试
  现有测试覆盖了所有纯逻辑函数（解析、过滤、文件 I/O），但 _execute_single_round 只在 consecutive_noops 测试中被 mock 间接覆盖，run_loop 的主循环边界条件（如 tasks_per_run 截断、所有 Agent 都失败时回退到 legacy 模式、git commit 失败后继续执行、非 git 备份模式的文件检测）完全没有测试覆盖。这些是最容易出 bug 的编排逻辑。建议用 pytest fixture + mock 的组合补上。

## 已完成

- [x] **#1** Agent 改动自动质量验证 (Round 1, 2026-06-16 18:23)
