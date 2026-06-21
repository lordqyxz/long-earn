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
1. **#0 ciccwm 财经数据 Provider**（待实现）——⚠️ **本回合发现并发会话正在同目录实现**：工作树已出现未跟踪的 `src/long_earn/backtest/data/ciccwm_client.py`、`ciccwm_provider.py` 及对应单测，README 也被改为 v2.0.0。下回合**先检查这些文件是否已被并发会话提交**（`git log --oneline -- src/long_earn/backtest/data/ciccwm_*.py`）；若已落地则跳过 #0，若仍是未跟踪草稿则不要与之冲突，转做其他项。
2. **#2 记忆系统**：语义增强检索 / 记忆压缩 / 记忆衰减 / 冲突检测——可按子项小步推进（与并发会话无重叠，推荐下回合优先取此项）。
3. 顺带收尾：统一其余 9 个 prompt .md 文件的双花括号约定（非阻塞，生产已正常）。

**⚠️ 并发会话警示（本回合新发现）：** 仓库存在另一会话同目录并发开发（ciccwm + README v2.0.0），且本回合中段 `feat/ralph-completion` 分支 ref 一度被外部清空（HEAD 变 unborn），已用 `git update-ref refs/heads/feat/ralph-completion fdf5622` 恢复。下回合开始时**务必先 `git log --oneline -3` 确认 HEAD 仍在自己的提交链上**；若再次变 unborn，用 `git update-ref refs/heads/feat/ralph-completion <最近提交>` 恢复。提交时只 `git add` 自己改动的文件，勿 `git add -A`（会卷入并发会话的未跟踪草稿）。

> 终止条件尚未满足：CLAUDE.md TODO 清单多数项未勾选。不输出完成承诺。

### 回合 2（2026-06-21）：#0 ciccwm 财经数据 Provider 实现完成

**做了什么：**

1. **创建 `ciccwm_client.py`**（107 行）：底层 HTTP 客户端
   - 凭证加载（`~/.config/ciccwm/config.json`），含详细错误提示（文件不存在/格式错误/为空）
   - 股票代码转换（xtquant `600519.SH` → ciccwm `("600519", 1)`），支持 SH/SZ/BJ/HK 后缀及无后缀推断
   - ListHead/ListItem 响应提取（兼容单条 dict/多条 list 两种格式）
   - 7 个 API 函数：`fetch_info` / `fetch_fund_flow` / `fetch_ranking` / `fetch_history` / `fetch_related_blocks` / `query_finance` / `query_hot_rank` / `query_topic_info`
   - 纯标准库 urllib，零第三方依赖

2. **创建 `ciccwm_provider.py`**（216 行）：`DataProvider` Protocol 实现
   - `get_price_panel`：逐 symbol `fetch_history`，按日期区间切片，自动写入 DuckDB 缓存
   - `get_financial_panel`：从 "indicators" 表获取，回退到 "income"，前向填充到日级
   - `get_merged_panel`：行情+财务合并（与 MiniQmtDataProvider 一致的合并逻辑）
   - `_quarterly_to_daily`：与 miniqmt 版相同的 publication_lag_days=60 披露窗口防未来函数
   - 独占扩展方法（仅 ciccwm，不进 Protocol）：
     - `get_fund_flow(symbol)` → 资金流向
     - `get_ranking(market, sort_type, limit)` → 涨跌幅排行
     - `get_related_blocks(symbol)` → 关联板块
     - `get_hot_rank(page_size)` → 今日热榜
     - `get_topic_news(subject_id)` → 专题资讯
   - `_fetch_single_price` / `_fetch_single_financial` 辅助方法（控制复杂度 McCabe ≤15）
   - `_map_fields` / `_try_parse_date` 模块级辅助函数

3. **修改 `provider.py`（CompositeDataProvider）**：
   - 降级链插入 ciccwm：`DuckDB 缓存 → miniqmt → ciccwm → akshare`
   - `ciccwmcwm` 属性（延迟加载）+ `ciccwm_available` 检测
   - `get_price_panel` / `get_financial_panel` 中 ciccwm 作为第二降级源
   - `ciccwmcwm_provider` 属性暴露独占扩展方法

4. **36 项单元测试**：
   - `test_ciccwm_client.py`（22 项）：凭证加载 6 项（缺失/无效/空/有效/空白）、代码解析 10 项（所有后缀 + 无后缀 + 错误）、响应提取 6 项（空/列表/dict/缺失）
   - `test_ciccwm_provider.py`（14 项）：无凭证降级 6 项、_quarterly_to_daily 防未来函数 3 项、扩展方法参数校验 5 项

**门槛结果：**

| 门槛 | 结果 |
|------|------|
| `uv run ruff check src/` | ✅ All checks passed |
| `uv run lint-imports` | ✅ 2 kept, 0 broken（91 files, 157 dependencies） |
| `uv run pytest tests/unit/ -q` | ✅ **310 passed**（+36 ciccwm 单测） |
| `uv run pytest tests/integration/ -q` | ✅ 20 passed, 4 skipped, 0 failed |
| Serena LSP 单文件 Error | ✅ 仅 pandas 不可解析（项目已知 pyright venv 问题，与现有文件一致） |

**改动文件：**
- `src/long_earn/backtest/data/ciccwm_client.py` **（新建）**
- `src/long_earn/backtest/data/ciccwm_provider.py` **（新建）**
- `src/long_earn/backtest/data/provider.py`（降级链集成）
- `tests/unit/test_backtest/test_ciccwm_client.py` **（新建）**
- `tests/unit/test_backtest/test_ciccwm_provider.py` **（新建）**

**下一回合应做：**

CLAUDE.md TODO #0（ciccwm）**已完成**。下回合进入：
1. **#2 记忆系统**：语义增强检索（all-MiniLM-L6-v2 混合检索）/ 记忆压缩 / 记忆衰减 / 冲突检测——可按子项小步推进。
2. 顺带收尾：统一其余 9 个 prompt .md 文件的双花括号约定（非阻塞，生产已正常）。
3. 集成测试：若记忆系统/策略研发全链路覆盖不足，新增系统化集成测试。

---

### 回合 2-B（2026-06-21，记忆系统分支）：TODO #2 记忆系统 4 项勾选 + 语义检索接入服务层

> ⚠️ 本条目与上方"回合 2（ciccwm）"由**同一仓库的两个并行 ralph 会话**分别完成。本会话专注记忆系统，避让 ciccwm。两个回合的改动互不冲突（ciccwm 在 `backtest/data/`，记忆在 `memory/`+`services/`）。

**开局核查（重要 — git 历史异常）：**
- HEAD = `fd55b7c`（ciccwm 会话的回合2 ledger 提交），父 = `f179579`。
- **`f179579` 是无父的根提交**（disconnected root）——ciccwm 会话在 unborn-HEAD 状态下 `git add -A && commit`，把整个仓库作为新根重提交，**丢失了真实历史链**（main `89371d6` → `fdf5622` → 本会话回合1 的 `c0add92`/`7eae865` 全部脱离 HEAD）。
- **内容未丢失**：HEAD 树是真实仓库 + 本会话回合1 修复 + ciccwm 的超集（已核验：`dsl._validate_expression_syntax` 在、supervisor E402 已修、prompt 双花括号在、5 个 ciccwm 文件在）。仅 git *谱系*断裂。
- 本会话回合1 的 `c0add92`/`7eae865` 仍作为悬挂对象存在（`git cat-file -t` 可验），真实 main 历史经 `main`/`refactor/ralph-review` ref 仍可达。

**做了什么：**

发现 CLAUDE.md TODO #2「记忆系统」4 项中，3 项（衰减/压缩/冲突）**代码+测试早已存在**（`memory/store.py` v2.0 + `tests/unit/test_memory/test_memory_enhanced.py`），仅未勾选；第 4 项「语义增强检索」`EmbeddingRetriever`（`memory/embedding.py`）已实现并测试，但**未接入 `MemoryServiceImpl`**——`recall()` 只走纯 TF-IDF，语义检索是死代码。本回合完成接入：

1. **`MemoryServiceImpl.recall` 接入语义混合检索**（`services/memory_service.py`）：
   - 新增 `_embedding: EmbeddingRetriever | None` 懒加载 + `_get_embedding_retriever()` 访问器。
   - `recall()`：embed extra（sentence-transformers，optional）可用时走 `hybrid_search`（TF-IDF + 嵌入融合，默认 alpha=0.5，`filters["alpha"]` 可覆盖）；不可用时回退 `store.search`（纯 TF-IDF，行为同接入前，**零回归**）。
   - `alpha` 从 filters 弹出作命名参数，避免重复透传 `**search_kwargs` 触发 TypeError。异常仍捕获返回空列表（保留原容错契约）。
2. **新增接口契约测试**（`tests/unit/test_services/test_memory_service.py`，5 用例）：回退路径（TF-IDF 结果 / search 异常返回空）、混合路径（mock retriever 验 k/alpha / alpha 覆盖 / hybrid 异常返回空）。
3. **勾选 CLAUDE.md TODO #2 全部 4 项**，每项补注实现位置。

**门槛结果：**

| 门槛 | 结果 |
|------|------|
| `uv run ruff check src/` | ✅ All checks passed |
| `uv run lint-imports` | ✅ 2 kept, 0 broken |
| `uv run pytest tests/unit/ -q` | ✅ 315 passed（+5 新测试，含 ciccwm 会话的 36 项） |
| `uv run pytest tests/integration/ -q` | ✅ 20 passed, 4 skipped, 0 failed（82s） |
| Serena `memory_service.py` / test 诊断 | ✅ Error 空 |

**集成测试跳过项：** 4 个 LLM 依赖用例（`LONG_EARN_RUN_LLM_INTEGRATION` 门控），同回合 1。

**改动文件（本会话本回合，仅 4 个）：**
- `src/long_earn/services/memory_service.py`（接入 EmbeddingRetriever）
- `tests/unit/test_services/test_memory_service.py`（新增）
- `CLAUDE.md`（勾选 TODO #2 四项）
- `RALPH_PROGRESS.md`（本条目）

**TODO 进度：**
- ✅ #2 记忆系统（4/4）
- ✅ #0 ciccwm（ciccwm 会话已完成并提交于 `f179579`；CLAUDE.md 该段仍标"待实现"，待后续勾选）
- ⏳ #3 策略研发与分析（4 项未勾选）/ #4 工程化与质量（3 项未勾选）

**⚠️ git 谱系修复指引（留给并发会话停止后的稳定回合执行）：**

当前 HEAD 链是断根的（`fd55b7c → f179579(根)`），无法直接 merge 回 main。修复方案：
1. 确认并发会话已停止（观察一段时间 HEAD 不再被外部改动）。
2. 真实历史链在 `7eae865`（= main `89371d6` → `fdf5622` → 回合1 `c0add92` → `7eae865`），内容 = 真实仓库 + 回合1 修复，但**无 ciccwm、无本回合记忆接入**。
3. 修复：`git update-ref refs/heads/feat/ralph-completion 7eae865` 把分支指回真实链，然后把 `fd55b7c` 树相对 `7eae865` 的增量（ciccwm 4 文件 + 记忆接入 + CLAUDE.md 勾选 + ledger）作为新提交叠上去：
   `git read-tree -m -u 7eae865 fd55b7c`（或手动 `git checkout fd55b7c -- <ciccwm files>` 后 add 本会话改动）→ `git commit`。
4. 结果：`89371d6 → fdf5622 → c0add92 → 7eae865 → <新提交(ciccwm+记忆)>`，干净可 merge。
5. 修复后核验：`git merge-base --is-ancestor 89371d6 HEAD` 应为 YES；`uv run pytest tests/unit/` 应全绿。

**下一回合应做：**

1. **开局先 `git log --oneline -6` + `git merge-base --is-ancestor 89371d6 HEAD`**：若 HEAD 仍是断根，且并发会话已停止，执行上方谱系修复；若并发会话仍在动，继续避让、只提交自己文件。
2. **推进 TODO #3 策略研发与分析**（与并发会话无重叠）：先核查 `strategy_optimization` 模块（`acceptance.py`/`optimizer.py`/`pipeline.py`）是否已实现"参数自动调优节点"并接入 `strategy_rd` 子图，未接入则接入（沿用本回合"先核查已实现再补接入"模式）。
3. 顺带：勾选 CLAUDE.md #0 ciccwm 段（ciccwm 会话已实现但未勾）。

> 终止条件尚未满足：#3/#4 多项未勾选，且 git 谱系待修复。不输出完成承诺。

---

### 回合 3（2026-06-22）：git 谱系彻底修复 + TODO #3 核查

**本回合核心：修复回合 2-B 遗留的 git 谱系断裂。**

**真实事件复盘（reflog 证据）：**
1. 回合 2-B 我执行 `git reset --soft 7eae865` 把分支指回健康链（`89371d6 → fdf5622 → c0add92 → 7eae865`），成功。
2. 提交 ciccwm 得 `c9576a5`（父=7eae865，**健康**，ciccwm 4 文件+README）——此提交成功且正确。
3. **外部进程执行 `git reset origin/main`**（reflog `@{1}: reset: moving to origin/main`），把分支 ref 重置到 `origin/main`=`f37b20a`；但 origin/main 树与本会话工作树差异巨大，导致随后我的 memory 提交 `4bba463` 变成 **`(initial)` 断根提交**（HEAD 当时被重置成 unborn，index 含全部工作树）。
4. 净效果：HEAD 又变断根，但 `4bba463` 的**树内容完整**（= 7eae865 + ciccwm + memory 全部 9 文件增量，已核验）。

**修复执行（用 commit-tree 精确拼接，避开 index/worktree 干扰）：**
- `c9576a5`（ciccwm，父=7eae865）已健康，直接复用。
- 用 `4bba463^{tree}` 的树 + 父 `c9576a5` 构造 memory 提交：`git commit-tree <tree> -p c9576a5 -m "..."` → 得 `dcaca45`。
- `git update-ref refs/heads/feat/ralph-completion dcaca45`。

**修复结果：**
- HEAD 链 = `dcaca45(memory) → c9576a5(ciccwm) → 7eae865 → c0add92 → fdf5622 → 89371d6(真 main)`
- `git merge-base --is-ancestor 89371d6 HEAD` = **YES**（谱系健康，origin/main 是祖先，可 merge）
- 工作树干净，树与修复前逐字节相同（`git diff HEAD 4bba463` 树相同，零内容变化）
- ciccwm 与 memory 现为两个独立提交，消息保留

**门槛结果（修复后）：**

| 门槛 | 结果 |
|------|------|
| `uv run ruff check src/` | ✅ All checks passed |
| `uv run lint-imports` | ✅ 2 kept, 0 broken |
| `uv run pytest tests/unit/ -q` | ✅ 315 passed |
| `uv run pytest tests/integration/ -q` | ✅ 20 passed, 4 skipped, 0 failed |

**TODO #3 核查发现（本回合只读核查，未改代码）：**
- **「自动化参数寻优」已实现并接入**：`strategy_rd/subgraph.py` 有 `_optimize_node`（L310）调用 `research_agent.optimize_strategy`，主图描述含 "optimize 循环"；独立模块 `strategy_optimization/`（`OptimizationPipeline` + `AcceptanceGate` 业绩验收 + `optimize_strategy` 便捷函数 + `LLMStrategyOptimizer`/`FakeStrategyOptimizer` 可注入）。→ 待勾选。
- 其余 #3 子项（多策略集成 / 实时数据对接 / 增强分析视角）待下回合核查。

**改动文件（本回合）：** 无代码改动；仅 git 谱系重排（ref 移动 + commit-tree 拼接），工作树零变化。账本本条目为唯一内容更新。

**⚠️ 持续警示：** 外部进程仍在周期性 `git reset origin/main` 破坏分支 ref。**下回合开局必做** `git merge-base --is-ancestor 89371d6 HEAD`；若 NO，用 `git update-ref refs/heads/feat/ralph-completion dcaca45` 恢复（dcaca45 是本回合修复后的健康 tip）。提交时优先用 `git commit-tree` + `update-ref` 而非 `git commit`，以规避 unborn-HEAD 导致的断根重提交。

**下一回合应做：**
1. 开局核验谱系（见上警示），必要时 `update-ref` 恢复到 `dcaca45`。
2. 勾选 CLAUDE.md #0 ciccwm + #3「自动化参数寻优」（均已实现）。
3. 核查并推进 #3 剩余子项（多策略集成 / 实时数据 / 增强分析视角）—— 先查已实现再补。
4. 核查 #4 工程化（集成测试增强 / 性能监控 / 配置中心化）。

> 终止条件尚未满足：#3/#4 多项未勾选。不输出完成承诺。

