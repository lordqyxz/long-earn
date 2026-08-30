# 统计验证门控加固 — 第二轮代码评审（2026-08-31）

> **评审对象**：`789c5aa` fix(gates): 强制 OOS 写回并加固 CSCV/DSR 诊断（相对 `f3430ac`）。
> **评审方式**：OpenCodeReview delegate 模式 v1.11.0 — `ocr delegate preview`（9 个可评审文件）+ `delegate rule`；宿主智能体双路只读复核（门控全路径 + 写回/CSCV/gap 深挖）；Critical/High 经主审源码确认。
> **背景**：对照 ADR-022、`docs/research/papers/statistical-gates-mapping.md`、业界 DSR/PBO/MinTRL。

**统计**：Critical 0 | High 4 | Medium 8 | Low 6（正文枚举；两路复核合并去重）。

**总体结论**：本轮 **写路径**落实「success 须 `oos_passed`」有效，CSCV 矩阵算法与 Bailey 块组合逻辑一致，诊断门未误升硬门、`skipped` 未静默当通过。剩余风险集中在 **飞轮读路径**（candidate≈success）、**DSR 输入错配**、**PBO 列对齐/回退**、**空 test 折下稳定性门折数不足**。

---

## Critical

无。

---

## High（4，均已亲验）

| # | 位置 | 问题 |
|---|------|------|
| H1 | `memory_service.py:113,120-145` + `strategy_develop_agent.py:189-202` | **candidate 污染飞轮读路径**：落盘 `backtest_success=not error`、检索仅 `min_sharpe`，不滤 `outcome`；develop 以「成功案例」注入 train-only 高夏普候选 |
| H2 | `research_agent.py:885-921` | **DSR 观测夏普与矩来源错配**：`observed_sharpe` 用 OOS mean，skew/kurt 用训练集 `daily_returns`；违背 mapping 升硬门前置「同源」 |
| H3 | `research_agent.py:231-235` + OOS payload 不含顶层 `sharpe_ratio` | **success 写回允许 metrics_json 污染**：LLM 可注入虚高 `sharpe_ratio` 并落盘；证据门不校验夏普真实性 |
| H4 | `research_agent.py:760-764` | **PBO 矩阵按最短列 index 截断**：跨策略日收益长度不一致时无日历对齐，CSCV 块语义失真 |

另见深挖：**gap 致空 test 折**时稳定性门可仅凭 1 折放行（`timeseries_split.py:44-47` + `WalkForwardStabilityGate` 单折放行）— 并入 Medium M-gap，威胁低于 H1–H4（需短窗+高 splits 组合）。

---

## Medium（摘要）

| # | 位置 | 问题 |
|---|------|------|
| M1 | `research_agent.py` PBO 分支 | 矩阵路径 `skipped`（T 不足）不回退 `pair_legacy` |
| M2 | `timeseries_split` + 稳定性门 | gap 空 test 折 → 折数不足仍可能硬门放行 |
| M3 | `research_agent.py:invoke` | 不清 `_oos_return_columns` / `_current_best_oos`，跨轮 PBO/合并基线污染 |
| M4 | `run_oos_gates` 内 `bt.run` | 不 `_register_trial`，仅跑 OOS 时 `N_eff` 偏低 |
| M5 | success 校验 | 仅 `oos_passed`，不强制 `backtest_reliable` |
| M6 | 测试 | 缺「OOS 硬门失败拒写 success」；`test_full_pipeline_with_oos` 未断言 `outcome==success` |
| M7 | outcome 枚举 | 非法拼写落入 success 分支（通常仍拒，信号弱） |
| M8 | DSR `n_observations` | 默认 252 / 折均 trading_days，非 OOS 日收益真实长度 |

---

## Low（摘要）

pair_legacy 非标准 CSCV；`run_walk_forward_parallel` 默认 gap=0 与 `run_oos` gap=5 不一致；gap 单测止于分割器；failure 路径零校验；指纹 16 hex 截断；`record_path_outcome` 默认 success。

---

## 关注点核对

| 关注点 | 结论 |
|--------|------|
| success 强制 oos_passed | **写路径通过**（`oos_passed is not True` 硬拒） |
| candidate 滥用 | **写路径 OK**；**读路径 High（H1）** |
| CSCV 矩阵算法 | **合格**；选路/对齐有 Medium/High |
| DSR 不误硬拒 | **通过**；输入错配 High（H2） |
| gap=5 贯通 | **RA→run_oos→Parallel→TimeSeriesSplit 贯通**；空折风险 Medium |
| skipped 当 passed | **硬门未误用** |

---

## 建议修复优先级（本轮不修，待用户指令）

1. **P0**：`search_experience` / develop「成功案例」过滤 `outcome==success`（或显式排除 candidate）
2. **P0**：success 写回 metrics 以证据覆盖用户字段（禁止 LLM 覆盖关键指标）
3. **P1**：DSR 矩与 T 尽量用 OOS 日收益；矩阵 CSCV 列日历对齐或长度不一致 → skipped；矩阵 skipped 回退 pair_legacy
4. **P1**：`invoke` 隔离 PBO/current_best；空 test 折计入稳定性失败；补拒写回归测

---

## OCR 元数据

- preview：`--from 789c5aa^ --to 789c5aa`，9 files（引擎 gap + gates + RA + 三测）
- 临时背景：`.ocr-gate-r2-bg.md`（可删）

---

## 修复处置（2026-08-31 测试加固）

| 评审项 | 处置 | 回归测 |
|--------|------|--------|
| **H1** candidate 污染飞轮读路径 | `search_experience(required_outcome="success")`；develop `_get_experience_context` 同步过滤 | `TestSearchExperienceRequiredOutcome`、`TestDevelopAgentRequiredOutcome`、`TestMemorySaveExperience.test_candidate_outcome_not_marked_success` |
| **H2** DSR 观测夏普与矩来源错配 | OOS mean + OOS 日收益矩（实现已合入） | `test_run_oos_gates_caches_evidence` 断言 `observed_sharpe_source==oos_mean` |
| **H3** success 写回 metrics 污染 | 证据字段覆盖 LLM `sharpe_ratio` 等受保护键 | `test_success_writeback_evidence_overrides_inflated_sharpe`、`test_full_pipeline_with_oos`（`outcome==success` + sharpe 来自 OOS mean） |
| **H4** PBO 矩阵按最短列截断 | 列长不一致 → `pair_legacy` | `test_pbo_mismatched_column_lengths_use_pair_legacy` |
| **M1** 矩阵 skipped 不回退 | `status==skipped` 时回退 `pair_legacy` | 同上 + `test_current_best_oos_updates_and_pbo_runs` |
| **M2** gap 空 test 折稳定性放水 | `len(sharpes) < n_folds` 硬拒 | `test_gap_large_can_yield_empty_test_fold`、`test_gap_empty_test_fold_fails_stability` |
| **M3** invoke 不清 PBO session | `invoke` 重置 `_oos_return_columns` 等 | `test_invoke_clears_oos_session_state` |
| **M6** 缺 OOS 失败拒写 / pipeline 弱断言 | 补拒写与全流程 outcome/sharpe | `test_record_path_outcome_rejects_when_oos_failed`、`test_full_pipeline_with_oos` |

**未在本轮闭合（仍登记）**：M4 `run_oos_gates` 内 `bt.run` 不 `_register_trial`；M5 不强制 `backtest_reliable`；M7 outcome 枚举；M8 DSR `n_observations` 默认 252。
