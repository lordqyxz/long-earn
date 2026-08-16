# ADR-010: 假设树精炼（Hypothesis Tree Refinement, HTR）

日期: 2026-06
状态: Accepted（Enhanced by ADR-015 + ADR-016；**编排控制器地位由 [ADR-018](018-think-on-graph-research-agent.md) 移交 ResearchAgent**：假设树 / held-out 合并门 / 六步节点保留为状态存储与可复用脚手架，不再是策略研发唯一合法编排）

## 背景

线性进化循环（15 节点 4 层循环）有六项结构性局限：无持久化研究状态（每轮覆盖前轮）、无分支探索（竞争假设丢弃）、无洞察累积（反思独立进行）、无 dev/test 分离（单回测区间易过拟合）、无基于证据的剪枝、反思是单次 ToT。借鉴 Arbor（[arXiv:2606.11926](https://arxiv.org/abs/2606.11926)）的 Hypothesis Tree Refinement 框架。

## 决策

以**持久化假设树**为研究状态，六步循环 Observe -> Ideate -> Select -> Dispatch -> Executor -> Backpropagate -> Decide 组织研究。

- **HypothesisTree / HypothesisNode**（`strategy_rd/hypothesis_tree.py`）：`id`/`parent_id`/`hypothesis`/`status`（pending/running/validated/pruned/merged/failed）/`dev_score`/`oos_score`/`insight`/`direction`；操作 `frontier()` / `best_node()` / `backpropagate_insight()` / `prune_subtree()` / `serialize()`。
- **混合持久化**（与 ADR-007 的关键交叉决策）：树本体独立 JSON Store（`HypothesisTreeStore`，按 run_id 隔离）——层级结构化研究状态不适合塞进 Substance 扁平 `content`，树操作是拓扑查询不是关键词/语义检索；树摘要（best 节点 + insight + 方向）回写 SubstanceStore 为 knowledge 物质，复用双通道检索做跨 run hot-start。`MemoryService` 相应扩展 `save_hypothesis_tree` / `search_hypothesis_trees`。
- **Held-out 合并门（保留为硬约束）**：dev 信号（训练集回测）自由探索；合并须过 Walk-Forward OOS（`BacktestService.run_oos`，expanding window），`oos_score > current_best + merge_threshold` 才 merge。ADR-015 在其上叠加三道统计门。
- **执行并行（2026-08 修正）**：原计划 LangGraph `Send` fan-out 是伪并行（同步 CPU 密集节点阻塞事件循环，且每候选重复取数）。修正为 `_executor_node` 单节点内部批量并行：①逐候选 optimize->develop（LLM IO 密集）；②`BacktestService.run_candidates` 批量回测（进程池 + 共享面板，受 ADR-008 B5 warmup 注入 / B6 diagnostics 保真约束）；③逐候选 AcceptanceGate。逃生口路径不进批量避免 double-gate；`htr_max_select` 语义从 fan-out 宽度变为批量回测宽度。
- **直接替换不回退**：旧线性流程删除，不保留 fallback。

## 后果

- 状态管理复杂度与 LLM 调用成本增加；`HTR_MAX_CYCLES` / `HTR_BRANCHING_FACTOR` / `HTR_MAX_DEPTH` / `HTR_MERGE_THRESHOLD` / `HTR_OOS_N_SPLITS` / `HTR_HOT_START` / `HYPOTHESIS_TREE_PATH` 可配。
- ADR-018 后六步顺序不再是唯一合法编排：HTR 子图降级为兼容脚手架（内部节点 develop / AcceptanceGate 被 ResearchAgent 工具复用）；假设树文件格式继续用于可视化探索路径。
