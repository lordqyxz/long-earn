# Ralph 循环进度账本

> 每回合末尾更新。记录做了什么、门槛结果、下一回合该做什么。

## 起点基线（2026-06-21，feat/ralph-completion 分支首回合前）

- 分支：`feat/ralph-completion`（从 `main` 快进合并而来，包含 origin/main 全部内容）
- 已提交：算子框架 + 策略优化 + 算子研发子图 + 单测，清理 11 个诊断脚本
- `.env` 已从 `.env.example` 创建（默认 ollama，无远端 API key），已 gitignore

### 门槛基线

| 门槛 | 结果 |
|------|------|
| `uv run lint-imports` | ✅ 2 kept, 0 broken |
| `uv run pytest tests/unit/ -q` | ✅ 274 passed |
| `uv run ruff check src/` | ❌ 6 errors（E402 导入顺序，集中在 strategy_rd_supervisor.py 等） |
| `uv run pytest tests/integration/ -q` | ⏳ 尚未跑，待首回合体检 |

### 首回合应做

1. 修 ruff 的 6 个 E402 错误（优先级 1）。
2. 跑 `tests/integration/` 摸清现状，区分"能跑 / 需门控跳过 / 缺测试"。
3. 之后按 CLAUDE.md TODO 清单顺序推进。

## 回合记录

（待各回合追加）
