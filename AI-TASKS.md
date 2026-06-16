# AI 任务列表
生成时间: 2026-06-16 18:46:33
运行次数: 2
最后运行: 2026-06-16 18:44:44
全局轮次: 1

共 6 个任务

## 待执行

- [ ] **#2** [high] [功能开发类] 自动管理 .gitignore 避免污染目标仓库
  控制器在目标目录生成 AI-TASKS.md、AI-CHANGELOG.md、ai-controller.log、.ai-controller-backups/ 等文件，但没有自动添加到 .gitignore。用户容易意外提交这些生成文件。在 `cli.py` 的 `run_loop` 启动时检查并自动追加这些路径到目标仓库的 .gitignore（或提示用户确认）。

- [ ] **#3** [high] [测试缺失类] 添加端到端集成测试
  当前所有测试都是 mock 单元测试，没有一次真正的集成测试覆盖完整管道（规划 -> 执行 -> git commit -> changelog 记录）。在 `tests/` 下添加 E2E 测试：创建临时目录，运行 controller 并使用 mock Agent 验证文件变动、changelog 写入、任务状态流转等关键路径。这是重构安全网的核心缺口。

- [ ] **#4** [medium] [功能开发类] 长任务执行中实时输出可见性
  `call_agent` 在 quiet 模式下只显示最后 5 行，用户无法看到 AI 正在做什么。对于 600 秒超时的任务体验极差。改进方案：在 subprocess stdout 到达时实时打印（streaming），而不是等进程结束才截取尾部。改 `ai_controller/agent.py` 中的 `call_agent` 函数。

- [ ] **#5** [medium] [修复类] 验证模块区分新旧测试失败
  `run_validation` 不区分本轮 AI 引入的测试失败和项目原有的失败。如果项目已有失败的测试，每次提交都会报告"质量验证发现问题"产生噪音。改进方案：在 AI 改动前先跑一次基线 pytest，与改动后的结果对比差异。改 `ai_controller/validation.py` 和 `cli.py`。

- [ ] **#6** [medium] [功能开发类] 任务执行增加可配置的并行度
  当前所有任务串行执行，对于无依赖关系的独立任务效率低下。在 `cli.py` 中增加 `--parallel N` 参数，支持最多 N 个任务并行执行。需要处理的任务包括：并行调度逻辑、git commit 冲突、changelog 写入互斥。大项目可显著缩短迭代总时间。

## 已完成

- [x] **#1** 修复 _extract_json_tasks 贪婪正则 bug (Round 1, 2026-06-16 18:46)
