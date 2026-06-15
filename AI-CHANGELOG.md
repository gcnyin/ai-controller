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

## Round 1 — 2026-06-15 11:22:33

**改动说明**: 激活 has_changes 死代码，在启动时检测并警告未提交的工作区改动，防止预存改动与 AI 改动混淆

**改动文件** (2 个):
- `I-CHANGELOG.md`
- `ai_controller.py`

*耗时 130.3s*

## Round 2 — 2026-06-15 11:25:02

**改动说明**: 修复 changelog 写入晚于 git commit 导致每轮记录缺失、最后一轮记录丢失的 bug，将 write_round_log 提前到 git_commit 之前执行

**改动文件** (2 个):
- `I-CHANGELOG.md`
- `ai_controller.py`

*耗时 147.7s*

## Round 1 — 2026-06-15 11:28:53

**改动说明**: 修复 build_round_prompt 未注入 prev_summary 导致多轮迭代缺乏历史上下文的 bug，使上一轮改动说明真正传递到下一轮 prompt 中

**改动文件** (2 个):
- `I-CHANGELOG.md`
- `ai_controller.py`

*耗时 62.5s*

## Round 2 — 2026-06-15 11:32:38

**改动说明**: 新增 --agent-args 参数支持，允许用户向 Agent 传递自定义参数（如指定模型、provider），解决了硬编码参数无法适配不同使用场景的问题

**改动文件** (2 个):
- `EADME.md`
- `ai_controller.py`

*耗时 222.9s*

## Round 3 — 2026-06-15 11:34:12

**改动说明**: 修复 `has_changes` 仅用 `git diff --quiet` 检测未暂存改动而遗漏暂存区和未跟踪文件的 bug，改用 `git status --porcelain` 全面检测所有未提交变更，防止用户预存的 `git add` 改动被悄无声息混入 AI 的 commit

**改动文件** (1 个):
- `i_controller.py`

*耗时 92.0s*

## Round 4 — 2026-06-15 11:38:00

**改动说明**: 新增 --resume 中断恢复功能：解析 AI-CHANGELOG.md 自动定位断点，从下一轮继续迭代并保留上轮改动上下文，解决中断后只能从头开始的痛点

**改动文件** (2 个):
- `EADME.md`
- `ai_controller.py`

*耗时 226.7s*

## Round 5 — 2026-06-15 11:41:36

**改动说明**: 为 --ext 文件过滤器增加实际校验能力 —— 新增 check_ext_filter 函数在每轮 Agent 运行后检查改动文件是否匹配用户指定的后缀，有不匹配时打印黄色警告并写入 changelog，同时顺手清理了 before_hash 死代码（每轮浪费一次 git rev-parse 调用但从未使用）

**改动文件** (1 个):
- `i_controller.py`

*耗时 213.5s*

## Round 6 — 2026-06-15 11:43:12

**改动说明**: 新增 extract_model_hint 函数从 --agent-args 中提取 --model/-m 参数值，写入 changelog 头部和启动横幅，补齐了 init_log 预留的 model_hint 参数但从未传入的功能缺口，让用户能一目了然每次迭代使用了哪个模型

**改动文件** (1 个):
- `i_controller.py`

*耗时 93.9s*

## Round 7 — 2026-06-15 11:46:05

**改动说明**: ` 行）时，将 summary 替换为明确的失败信息，格式为 `"Agent 异常退出（返回码 {rc}），未提供改动说明"`。

**改动文件** (1 个):
- `i_controller.py`

*耗时 170.9s*

## Round 1 — 2026-06-15 11:48:34

改动说明: Agent 执行超时

改动文件 (1 个):
- `i_controller.py`

*耗时 10.0s*

## Round 2 — 2026-06-15 11:48:44

改动说明: Agent 执行超时

改动文件: 无（本轮无代码变更）

*耗时 10.0s*

## Round 3 — 2026-06-15 11:48:54

改动说明: Agent 执行超时

改动文件 (1 个):
- `I-CHANGELOG.md`

*耗时 10.0s*

## Round 4 — 2026-06-15 11:49:04

改动说明: Agent 执行超时

改动文件: 无（本轮无代码变更）

*耗时 10.0s*

## Round 5 — 2026-06-15 11:49:14

改动说明: Agent 执行超时

改动文件 (1 个):
- `I-CHANGELOG.md`

*耗时 10.0s*

