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

```bash
# pi 跑 10 轮
python ai_controller.py ./my-project --agent pi --max-rounds 10

# opencode 无限循环
python ai_controller.py ./my-project --agent opencode --max-rounds 0

# claude 只改 Python 文件，跑 5 轮
python ai_controller.py ./my-project --agent claude --ext .py --max-rounds 5

# codex 跑 3 轮
python ai_controller.py ./my-project --agent codex --max-rounds 3

# pi 指定模型和 provider
python ai_controller.py ./my-project --agent pi --agent-args '--model gpt-4o --provider openai'

# 中断后恢复，从上次断点继续
python ai_controller.py ./my-project --agent pi --max-rounds 10 --resume

# 只保留最近 5 个备份，自动清理旧备份
python ai_controller.py ./my-project --agent pi --max-rounds 100 --keep-backups 5
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
| `--agent-args` | 传递给 Agent 的额外参数，用引号包裹 | - |
| `--resume` | 中断后恢复：读取 changelog 从下一轮继续 | false |
| `--keep-backups` | 只保留最近 N 个备份，旧备份自动清理（0=不限制） | 0 |

## 工作流程

```
┌──────────────────────────┐
│ 1. 备份当前代码 (可选)    │
└───────────┬──────────────┘
            ▼
┌──────────────────────────┐
│ 2. 构建提示词             │
│   "做一轮改进，直接改文件" │
└───────────┬──────────────┘
            ▼
┌──────────────────────────┐
│ 3. 调用 Agent 非交互模式  │
│   pi -p "..."             │
│   opencode run "..."      │
│   claude -p "..."         │
│   codex exec "..."        │
└───────────┬──────────────┘
            ▼
┌──────────────────────────┐
│ 4. 检查 git diff          │
│   有改动 → commit         │
│   无改动 → noop 计数+1    │
└───────────┬──────────────┘
            ▼
      下一轮 ↻ (或无改动3次退出)
```

## 中断恢复

Ctrl+C 中断后，可以带 `--resume` 重新运行，控制器会读取 `AI-CHANGELOG.md` 自动找到上次断点，从下一轮继续。

```bash
# 比如跑 10 轮，第 5 轮被中断了
python ai_controller.py ./my-project --agent pi --max-rounds 10 --resume
# 输出: 恢复模式 : 从第 6 轮继续（上次完成 5 轮）
```

- 恢复时会把上一轮的改动说明传给 AI，避免重复做相同的事
- 如果 changelog 中已无更多轮次可恢复（如已完成全部轮次），会直接退出

## 退出条件

- 达到 `--max-rounds` 上限
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

**改动说明**: 修复了 login 函数中 token 为空的空指针异常

**改动文件** (2 个):
- `src/auth/login.py`
- `tests/test_login.py`

*耗时 12.3s*

## Round 2 — 2026-06-15 10:32:45
...
```

- 如果日志文件已存在，会追加写入（不会覆盖）
- prompt 要求 AI 在输出末尾给出 `SUMMARY: <一句话说明>`，控制器解析后写入日志
- 上一轮的改动说明会传给下一轮，让 AI 知道之前做了什么、避免重复
