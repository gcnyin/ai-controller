# AI 自迭代改动记录

- **开始时间**: 2026-06-15 10:59:16
- **Agent**: pi
- **目标目录**: /root/dev/ai-controller

---

## Round 1 — 2026-06-15 11:06:21

**改动说明**: 修复 build_round_prompt 空壳函数，使其真正注入轮次信息和上轮改动上下文，让多轮迭代不再重复做相同改动

**改动文件** (4 个):
- `.gitignore`
- `README.md`
- `ai_controller.py`
- `AI-CHANGELOG.md`

*耗时 38.8s*

## Round 2 — 2026-06-15 11:07:39

**改动说明**: 修复 get_git_diff_summary 在 git_commit 之后调用导致 diff 统计始终为空的 bug，将其移到 commit 之前执行，使用户能正确看到每轮的文件变更统计

**改动文件** (2 个):
- `I-CHANGELOG.md`
- `ai_controller.py`

*耗时 76.1s*

## Round 1 — 2026-06-15 11:16:45

**改动说明**: 修复 call_agent 超时机制的关键缺陷 —— 用 communicate() 替代 for 循环 + wait，确保 Agent 进程挂死时超时能真正生效，防止控制器永久卡住

**改动文件** (2 个):
- `I-CHANGELOG.md`
- `ai_controller.py`

*耗时 203.6s*

## Round 2 — 2026-06-15 11:19:21

**改动说明**: 修复了 Agent 返回异常时跳过文件变更检测的 bug —— 将 get_changed_files 调用提前到成功/失败判断之前，确保 Agent 报错前已做的文件改动不被丢弃，同时修复了 consecutive_noops 在失败时不递增导致退出条件永远无法触发的问题

**改动文件** (2 个):
- `I-CHANGELOG.md`
- `ai_controller.py`

*耗时 154.3s*

