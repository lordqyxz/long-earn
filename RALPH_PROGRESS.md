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

### 回合 1（2026-06-21）：质量门槛全绿 + 集成测试失败清零

**做了什么：**

1. **修复 ruff 6 个错误**（`src/` 全绿）：
   - `strategy_rd_supervisor.py`：E402——常量 `_GOOD_SHARPE_THRESHOLD` 移到导入语句之后。
   - `provider.py`：PLR2004——新增 `_COMPACT_DATE_LEN = 8` 常量替代魔法值。
   - `miniqmt_provider.py`：B007（`std_col`→`_std_col`）、PLR2004（`> 2`→`> _BALANCE_BASE_COL_COUNT`）、PLR0912×2（`_fetch_financials` 17→拆出 `_build_financial_base`/`_merge_balance_equity`；`_compute_derived_financials` 14→拆出 `_fill_symbol_derived`/`_fill_gross_margin`/`_fill_yoy_growth`/`_fill_roe`）。`_EQUITY_FIELDS` 提为模块级常量。
2. **修复 3 个 prompt_loader 集成测试失败**：`strategy_research_prompt.md` 正文变量从单花括号 `{var}` 改为双花括号 `{{var}}`（对齐 CLAUDE.md 约定与单测 `test_prompt_loader.py`）。根因：所有 10 个 prompt .md 都用单花括号，而加载器 `_extract_variables` 只识别双花括号，自动提取恒为空；生产侧靠显式传 `input_variables` 规避，集成测试走自动提取即崩。本回合仅修被测的 `strategy_research_prompt.md`，其余 9 个文件生产正常（显式传参），留作后续统一。
3. **修复 `test_backtest_detects_syntax_error`**：DSL 解析器新增 `_validate_expression_syntax`，对 filter `condition` 在解析期做 `ast.parse(mode="eval")` fail-fast。此前畸形表达式（如 `x > >`）只在逐 bar 求值时被容忍，触发长达数年的无效回测（6 分钟）且不报错；现在解析期即抛 `ValueError`→`backtest_service.run` 返回 `error`，测试 0.67s 通过。符合 ADR-005 Agent 友好目标。
4. **门控 4 个 LLM 依赖集成测试**：`test_develop_backtest.py` 的 `TestDevelop`（3）与 `TestDevelopAndBacktest`（1）加 `@requires_llm`（`LONG_EARN_RUN_LLM_INTEGRATION` env 开关，默认跳过）。`TestBacktest`（known_good/detect_syntax）不依赖 LLM，仍正常运行。
5. 顺手修测试文件一处 SIM108（if-else→三元）。

**门槛结果：**

| 门槛 | 结果 |
|------|------|
| `uv run ruff check src/` | ✅ All checks passed |
| `uv run lint-imports` | ✅ 2 kept, 0 broken |
| `uv run pytest tests/unit/ -q` | ✅ 274 passed |
| `uv run pytest tests/integration/ -q` | ✅ 20 passed, 4 skipped, 0 failed（61s） |

**集成测试跳过项（已记录）：**
- `test_develop_backtest.py::TestDevelop::test_develop_generates_valid_code` ×3（利润增长/动量/低估值）——依赖真实 LLM 在线生成策略代码，默认 ollama 未启用，设 `LONG_EARN_RUN_LLM_INTEGRATION=1` 启用。
- `test_develop_backtest.py::TestDevelopAndBacktest::test_develop_then_backtest[动量]`——同上，端到端 develop→backtest→refine 需真实 LLM。

**改动文件：**
- `src/long_earn/strategy_rd/agents/strategy_rd_supervisor.py`
- `src/long_earn/backtest/data/provider.py`
- `src/long_earn/backtest/data/miniqmt_provider.py`
- `src/long_earn/strategy_rd/agents/strategy_research_prompt.md`
- `src/long_earn/backtest/engine/dsl.py`
- `tests/integration/test_develop_backtest.py`

**下一回合应做：**

优先级 1（体检失败）已清零。进入 CLAUDE.md TODO 清单（按文档顺序）：
1. **#0 ciccwm 财经数据 Provider**（待实现）——最大块，见 ADR-006，需新增 `ciccwm_client.py` + `ciccwm_provider.py`，接入 `CompositeDataProvider` 降级链。建议本回合或下回合启动。
2. **#2 记忆系统**：语义增强检索 / 记忆压缩 / 记忆衰减 / 冲突检测——可按子项小步推进。
3. 顺带收尾：统一其余 9 个 prompt .md 文件的双花括号约定（非阻塞，生产已正常）。

> 终止条件尚未满足：CLAUDE.md TODO 清单多数项未勾选。不输出完成承诺。

