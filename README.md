# AI 自迭代控制器

调用 `pi` / `opencode` / `claude` / `codex` 让 AI 自动循环改进代码库。控制器只负责编排循环，实际修改由 Agent 完成。

## 前提

确保至少安装了其中一个 Agent：

```bash
# pi（你现在用的这个）
npm i -g @earendil-works/pi-coding-agent

# opencode
npm i -g @opencode-ai/opencode

# claude (Claude Code)
npm i -g @anthropic-ai/claude-code

# codex (OpenAI Codex)
npm i -g @openai/codex
```

## 用法

### 默认模式（先规划再执行）

```bash
# pi 跑 10 轮（先让 AI 生成任务列表，再逐条执行）
python ai_controller.py ./my-project --agent pi --max-rounds 10

# opencode 无限循环
python ai_controller.py ./my-project --agent opencode --max-rounds 0

# claude 只改 Python 文件
python ai_controller.py ./my-project --agent claude --ext .py --max-rounds 5

# 只生成任务列表，不执行（保存到 AI-TASKS.md）
python ai_controller.py ./my-project --agent pi --plan-only

# 自动恢复（检测到 AI-TASKS.md 存在时自动从未完成任务继续）
python ai_controller.py ./my-project --agent pi --max-rounds 10

# 重新生成任务列表
python ai_controller.py ./my-project --agent pi --replan
```

### 传统模式（跳过规划，每轮 AI 自行选择）

```bash
python ai_controller.py ./my-project --agent pi --max-rounds 10 --no-plan
```

## 参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `directory` | 目标代码目录（必填） | - |
| `--agent` | 选 pi / opencode / claude / codex | pi |
| `--max-rounds` | 最大轮数，0=无限 | 10 |
| `--ext` | 只处理指定后缀，逗号分隔 | 全部 |
| `--timeout` | Agent 单轮超时秒数 | 600 |
| `--sleep` | 每轮间隔秒数 | 2.0 |
| `--no-backup` | 不备份 | false |
| `--no-git` | 不自动 git commit | false |
| `--agent-args` | 传递给 Agent 的额外参数 | - |
| `--replan` | 强制重新生成任务列表 | false |
| `--keep-backups` | 只保留最近 N 个备份（0=不限制） | 0 |
| `--no-plan` | 跳过规划阶段，使用传统逐轮模式 | false |
| `--plan-only` | 只生成任务列表，不执行 | false |
| `--replan` | 强制重新生成任务列表（备份旧文件为 .bak） | false |
| `--tasks-per-run` | 每次运行最多处理 N 个任务后退出（0=不限制） | 0 |
| `--dry-run` | 预览模式：打印执行计划，不实际修改文件 | false |
| `--keep-backups` | 只保留最近 N 个备份（0=不限制） | 0 |

## 配置文件

可在项目根目录下放置 `.ai-controller.toml` 或 `.ai-controller.yaml` 预设参数，
命令行参数优先级高于配置文件。

```toml
# .ai-controller.toml
agent = "pi"
max_rounds = 20
ext = ".py,.ts"
timeout = 300
```

## 工作流程

### 默认模式（v2.2）

```
┌──────────────────────────────┐
│ 阶段 1: 规划                  │
│ AI 扫描代码库，生成 JSON 格式  │
│ 的完整任务列表（按优先级排序）  │
└───────────┬──────────────────┘
            ▼
┌──────────────────────────────┐
│ 保存任务列表到 AI-TASKS.md    │
│ 格式：待执行/已完成            │
└───────────┬──────────────────┘
            ▼
┌──────────────────────────────┐
│ 阶段 2: 逐条执行              │
│ ┌────────────────────────┐   │
│ │ 读取下一个待执行任务     │   │
│ │ → 备份（可选）          │   │
│ │ → 构建任务 prompt       │   │
│ │ → 调用 Agent 执行       │   │
│ │ → 检查改动，commit      │   │
│ │ → 标记任务为已完成       │   │
│ │ → 循环                  │   │
│ └────────────────────────┘   │
└──────────────────────────────┘
```

### 传统模式（--no-plan）

```
┌──────────────────────────┐
│ 每轮：备份 → AI 自行选择   │
│ 一个改进点 → 修改文件      │
│ → 记录日志 → 下一轮        │
└──────────────────────────┘
```

## 任务列表文件 (AI-TASKS.md)

规划阶段会在目标目录生成 `AI-TASKS.md`，格式如下：

```markdown
# AI 任务列表
生成时间: 2026-06-15 10:30:00

共 5 个任务

## 待执行

- [ ] **#1** [high] [修复类] 修复 token 为空的空指针异常
  修改 src/auth/login.py 中的 validate_token 函数，增加空值检查

- [ ] **#2** [medium] [功能开发类] 添加请求日志中间件
  ...

## 已完成

- [x] **#3** 修复 login bug (Round 1)
```

- 检测到 `AI-TASKS.md` 存在时会自动从中加载未完成任务继续执行
- 可在执行前手动编辑该文件调整优先级或删除不需要的任务

## 中断恢复

Ctrl+C 中断后，直接重新运行相同命令即可自动恢复：

```bash
# 默认模式：自动检测 AI-TASKS.md，从未完成任务继续执行
python ai_controller.py ./my-project --agent pi --max-rounds 10

# 传统模式（--no-plan）：每次从第 1 轮重新开始
python ai_controller.py ./my-project --agent pi --max-rounds 10 --no-plan
```

如需重新规划任务列表，使用 `--replan` 参数。

## 退出条件

- 达到 `--max-rounds` 上限
- 所有任务执行完毕（默认模式）
- 连续 3 轮无代码改动
- Ctrl+C 手动中断

## 改动日志

每轮跑完后会在目标目录生成 `AI-CHANGELOG.md`，记录每轮的改动说明、文件列表和耗时：

```markdown
# AI 自迭代改动记录

- **开始时间**: 2026-06-15 10:30:00
- **Agent**: pi

---

## Round 1 — 2026-06-15 10:30:15

**改动说明**: [任务#1] 修复了 login 函数中 token 为空的空指针异常

**改动文件** (2 个):
- `src/auth/login.py`
- `tests/test_login.py`

*耗时 12.3s*

## Round 2 — 2026-06-15 10:32:45
...
```
