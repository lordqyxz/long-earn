# 任务：测试并完善 long_earn 交易策略系统

你正在一个自我进化的量化交易系统 `long_earn` 上工作。本回合的目标是**持续推进系统走向"充分完成"**。

## 第 0 步：读取上次进度（每次必做）

1. `git log --oneline -20` 与 `git status` —— 看上回合改了什么、是否有未提交工作。
2. 读取 `RALPH_PROGRESS.md`（若存在）—— 这是上回合写入的进度账本。若不存在则创建。
3. 读取 `CLAUDE.md` 的「开发待办 (TODO)」章节 —— 这是待完成功能清单。

## 第 1 步：质量门槛体检（判断"是否充分完成"的客观依据）

依次运行，记录每项通过/失败：

```sh
uv run pytest tests/unit/ -v           # 单元测试全绿
uv run ruff check src/                 # 风格 + 复杂度
uv run lint-imports                    # 架构依赖契约
```

对刚编辑过的文件，用 Serena `mcp__serena__get_diagnostics_for_file` 验证 Error 级别诊断为空。
集成测试 `tests/integration/` 需 `.env`，若环境不可用则跳过并在进度账本注明。

## 第 2 步：决定本回合做什么（按优先级，取第一个未完成项）

1. **修复体检中暴露的失败**（测试红、ruff 报错、lint-imports broken、Serena Error）—— 优先级最高。
2. **推进 CLAUDE.md TODO 清单**中未勾选的项，按文档顺序逐个完成。每完成一项，在 `RALPH_PROGRESS.md` 记录：日期、项名、改动文件、验证结果。
3. 若 TODO 全部完成且门槛全绿，进入"加固阶段"：补齐 `tests/unit/` 中覆盖不足的核心链路（引擎主流程、风控、Walk-Forward、安全求值器、DSL 解析），或补充 ADR / 文档。

## 第 3 步：执行与验证

- 实现后**必须**重新运行第 1 步的相关门槛，确保本次改动未引入回归。
- 所有新代码遵循 CLAUDE.md 编码规范（Python 3.11 严格、类型注解、中文注释、依赖注入、88 字符行宽、McCabe ≤15）。
- 不可破坏架构依赖方向：`tools → services → domain`，`backtest.data` 不依赖上层。

## 第 4 步：提交并写进度账本

- `git add` 相关改动并提交（中文 commit message，遵循 `feat/fix/test/chore(scope): 描述` 格式）。**不要**提交诊断脚本 `scripts/diag_*.py` 除非确属必要。
- 更新 `RALPH_PROGRESS.md`：本回合做了什么、门槛结果、下一回合应做什么。

## 终止条件

当且仅当**同时满足**以下全部条件时，输出完成承诺（否则不要输出）：

- CLAUDE.md TODO 清单所有项已勾选完成；
- `uv run pytest tests/unit/`、`uv run ruff check src/`、`uv run lint-imports` 全绿；
- `RALPH_PROGRESS.md` 已记录每个 TODO 项的完成证据。

完成时输出（注意必须用 promise 标签包裹）：

<promise>SYSTEM FULLY COMPLETE</promise>

未完成则正常结束回合，等待下一轮同一 prompt 触发。每回合聚焦推进 1~2 个 TODO 项，宁可小步可验证，不要一次铺开太大。
