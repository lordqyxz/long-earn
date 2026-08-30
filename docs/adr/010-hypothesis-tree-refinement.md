---
id: 10
title: 假设树精炼（Hypothesis Tree Refinement）
status: Deprecated
date: 2026-06
summary: 以持久化假设树组织六步 HTR 研究循环；编排控制器已由 ADR-018 取代。
deprecated_note: 编排已删（2026-08-31）；假设树状态与合并及统计验证见 ADR-022。
related: ["ADR-018", "ADR-022", "ADR-015"]
---

# ADR-010: 假设树精炼（Hypothesis Tree Refinement）


> 下文「决策」为历史原文（当时以六步循环为唯一编排）。现行策略研发控制面以 ADR-018 ResearchAgent 为准；勿再新增对本 ADR 编排路径的依赖。

## 背景

线性进化循环（15 节点 4 层循环）存在六项结构性局限：无持久化研究状态（每轮覆盖前轮）；无分支探索（竞争假设被丢弃）；无洞察累积（反思独立进行）；无 dev/test 分离（单回测区间易过拟合）；无基于证据的剪枝；反思为单次 Tree-of-Thought。借鉴 Arbor（[arXiv:2606.11926](https://arxiv.org/abs/2606.11926)）的 Hypothesis Tree Refinement 框架。

**废弃说明（2026-08-30）**：六步 HTR **编排实现**已于 2026-08-31 删除。**仍生效、不随本 ADR 废弃的部分**：假设树 JSON 状态（可视化 / 探索路径）与 held-out 合并及统计验证门（ADR-022；失败反馈见 ADR-015 Tier A/B），由 ResearchAgent `run_oos_gates` 等消费。编排控制器已由 ADR-018 移交；LLM 控制流违例由 ADR-021 要求随退役消除。

## 决策

我们将以**持久化假设树**为研究状态，以六步循环 Observe → Ideate → Select → Dispatch → Executor → Backpropagate → Decide 组织研究。

- **HypothesisTree / HypothesisNode**（`strategy_rd/hypothesis_tree.py`）：`id`/`parent_id`/`hypothesis`/`status`（pending/running/validated/pruned/merged/failed）/`dev_score`/`oos_score`/`insight`/`direction`；操作 `frontier()` / `best_node()` / `backpropagate_insight()` / `prune_subtree()` / `serialize()`。
- **混合持久化**（与 ADR-007 的关键交叉决策）：树本体独立 JSON Store（`HypothesisTreeStore`，按 run_id 隔离）——层级结构化研究状态不适合纳入 Substance 扁平 `content`，树操作为拓扑查询而非关键词/语义检索；树摘要（best 节点 + insight + 方向）回写 SubstanceStore 为 knowledge 物质，复用双通道检索实现跨 run 热启动。`MemoryService` 相应扩展 `save_hypothesis_tree` / `search_hypothesis_trees`。
- **Held-out 合并门（保留为硬性约束）**：dev 信号（训练集回测）自由探索；合并须通过 Walk-Forward OOS（`BacktestService.run_oos`，expanding window），`oos_score > current_best + merge_threshold` 方可 merge。统计门用法（Walk-Forward 硬性门控；Deflated Sharpe Ratio, DSR 与 Probability of Backtest Overfitting, PBO 诊断）见 ADR-022。
- **执行并行**：原计划 LangGraph `Send` 扇出分发为伪并行（同步 CPU 密集节点阻塞事件循环，且每候选重复取数）。修正为 `_executor_node` 单节点内部批量并行：①逐候选 optimize→develop（LLM IO 密集）；②`BacktestService.run_candidates` 批量回测（进程池 + 共享面板，受 ADR-008 B5 warmup 注入 / B6 diagnostics 保真约束）；③逐候选 AcceptanceGate。例外处理路径不进批量，避免双重门控；`htr_max_select` 语义从扇出宽度变为批量回测宽度。
- **直接替换无回退**：旧线性流程删除，不保留回退路径。

## 后果

**正面（历史）**

- 持久化假设树支持分支探索与基于证据的剪枝，研究状态可跨轮累积。
- Held-out 合并门与统计验证门（ADR-022）保留，防止过拟合策略合并。
- 假设树 JSON 状态格式保留，供可视化与探索路径追溯。

**负面**

- 状态管理复杂度与 LLM 调用成本增加。
- `htr_subgraph` / `strategy_rd/agents/` 等 HTR 编排实现已删除；CLI / app research 端点已迁至 ResearchAgent，不得再新增对遗留编排路径的依赖。
- `HTR_*` / `HYPOTHESIS_TREE_PATH` 可配项随编排退役一并评估去留。

**中性**

- PBO 须按 ADR-022 迁入 ToG 路径或显式降级，不得随 HTR 无声消失。
- 编排控制器由 ADR-018 取代；HTR 编排实现已删除，假设树状态与合并门仍由 ResearchAgent 消费。

## 关联

- 编排取代: ADR-018（ResearchAgent）
- 统计验证: ADR-022、ADR-015 Tier A/B
- LLM 分层: ADR-021
