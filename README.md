# AI 自迭代控制器

调用 `pi` / `opencode` / `claude` / `codex` 让 AI 自动循环改进代码库。控制器只负责编排循环，实际修改由 Agent 完成。

## 前提

确保至少安装了其中一个 Agent：

```bash
# pi
npm i -g @earendil-works/pi-coding-agent

# opencode
npm i -g @opencode-ai/opencode

# claude (Claude Code)
npm i -g @anthropic-ai/claude-code

# codex (OpenAI Codex)
npm i -g @openai/codex
```

## 安装

```bash
# 克隆仓库
git clone <repo-url> && cd ai-controller

# 使用 uv 创建虚拟环境并安装
uv venv --python 3.10
uv pip install -e ".[all,test]"
```

安装后通过 `uv run ai-controller` 运行，或激活虚拟环境后直接使用：

```bash
source .venv/bin/activate
ai-controller --help

# 也可以直接用 python 模块方式运行
python -m ai_controller --help
```

Python 3.10+ 均可使用（高版本自动启用内置 `tomllib`，低版本需安装 `tomli`）。

## 用法

### 默认模式（先规划再执行）

```bash
# pi 跑 10 轮（先让 AI 扫描代码库生成任务列表，再逐条执行）
uv run ai-controller ./my-project --agent pi --max-rounds 10

# opencode 无限循环
uv run ai-controller ./my-project --agent opencode --max-rounds 0

# 只生成任务列表，不执行（保存到 AI-TASKS.md）
uv run ai-controller ./my-project --agent pi --plan-only

# 自动恢复（检测到 AI-TASKS.md 存在时自动从未完成任务继续）
uv run ai-controller ./my-project --agent pi --max-rounds 10

# 重新生成任务列表
uv run ai-controller ./my-project --agent pi --replan

# 预览模式（打印执行计划，不实际修改文件）
uv run ai-controller ./my-project --agent pi --dry-run

# 传递额外参数给 Agent（如指定模型）
uv run ai-controller ./my-project --agent pi --agent-args "--model gpt-4"
```

## 参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `directory` | 目标代码目录（必填） | - |
| `--agent` | 选 pi / opencode / claude / codex | pi |
| `--max-rounds` | 最大轮数，0=无限 | 10 |
| `--timeout` | Agent 单轮超时秒数 | 600 |
| `--sleep` | 每轮间隔秒数 | 2.0 |
| `--no-backup` | 不备份（Git 仓库自动跳过备份） | false |
| `--agent-args` | 传递给 Agent 的额外参数（引号包裹） | - |
| `--keep-backups` | 只保留最近 N 个备份（0=不限制） | 0 |
| `--plan-only` | 只生成任务列表 AI-TASKS.md，不执行 | false |
| `--replan` | 强制重新生成任务列表（备份旧文件为 .bak） | false |
| `--dry-run` | 预览模式：打印执行计划，不实际修改文件 | false |

## 配置文件

可在目标项目根目录下放置 `.ai-controller.toml` 或 `.ai-controller.yaml` 预设参数，命令行参数优先级高于配置文件。

```toml
# .ai-controller.toml
agent = "pi"
max_rounds = 20
timeout = 300
sleep = 1.0
keep_backups = 5
```

## 工作流程

```
+------------------------------+
| 阶段 1: 规划                  |
| AI 扫描代码库，生成 JSON 格式  |
| 的完整任务列表（按优先级排序）  |
+-------------+----------------+
              |
              v
+------------------------------+
| 保存任务列表到 AI-TASKS.md    |
| 格式：待执行 / 已完成          |
+-------------+----------------+
              |
              v
+------------------------------+
| 阶段 2: 逐条执行              |
| +----------------------------+
| | 读取下一个待执行任务        |
| | -> 备份（可选）             |
| | -> 构建任务 prompt          |
| | -> 调用 Agent 执行         |
| | -> 检查改动，commit         |
| | -> 标记任务为已完成         |
| | -> 循环                    |
| +----------------------------+
+------------------------------+
```

## 任务列表文件 (AI-TASKS.md)

规划阶段会在目标目录生成 `AI-TASKS.md`，格式如下：

```markdown
# AI 任务列表
生成时间: 2026-06-15 10:30:00
运行次数: 1
最后运行: 2026-06-15 10:30:00
全局轮次: 0

共 5 个任务

## 待执行

- [ ] **#1** [high] [bug fix] 修复 token 为空的空指针异常
  修改 src/auth/login.py 中的 validate_token 函数，增加空值检查

- [ ] **#2** [medium] [new feature] 添加请求日志中间件
  ...

## 已完成

- [x] **#3** 修复 login bug (Round 1, 2026-06-15 10:30:55)
```

- 检测到 `AI-TASKS.md` 存在时会自动从中加载未完成任务继续执行
- 可在执行前手动编辑该文件调整优先级或删除不需要的任务
- 每次运行会在文件头部记录运行次数、最后运行时间和全局轮次

## 中断恢复

Ctrl+C 中断后，直接重新运行相同命令即可自动恢复：

```bash
# 自动检测 AI-TASKS.md，从未完成任务继续执行
uv run ai-controller ./my-project --agent pi --max-rounds 10
```

如需重新规划任务列表，使用 `--replan` 参数。

## Git 集成

目标目录为 Git 仓库时：

- **自动 commit**：每轮改动自动 `git add -A && git commit`
- **跳过备份**：Git 仓库已有版本历史，自动跳过全量备份
- **自动 stash**：检测到未提交的用户改动时自动 stash 隔离，执行完毕后自动恢复
- **自动 .gitignore**：首次运行时自动将 `AI-TASKS.md`、`AI-CHANGELOG.md`、`ai-controller.log`、`.ai-controller-backups/` 追加到 `.gitignore`

## 退出条件

- 达到 `--max-rounds` 上限
- 所有任务执行完毕
- 单个任务连续 3 轮无代码改动（自动跳过该任务）
- Ctrl+C 手动中断

## 改动日志

每轮跑完后会在目标目录生成 `AI-CHANGELOG.md`，记录每轮的改动说明、文件列表和耗时：

```markdown
# AI 自迭代改动记录

- 开始时间: 2026-06-15 10:30:00
- Agent: pi

---

## 运行 #1 - 2026-06-15 10:30:05

## Round 1 - 2026-06-15 10:30:15

改动说明: [任务#1] 修复了 login 函数中 token 为空的空指针异常

改动文件 (2 个):
- `src/auth/login.py`
- `tests/test_login.py`

*耗时 12.3s*
```

同时生成 `ai-controller.log` 记录 DEBUG 级别的完整日志，方便排查问题。
