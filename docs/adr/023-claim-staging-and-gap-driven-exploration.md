---
id: 23
title: Claim 一等公民、审核分层与缺口驱动探索
status: Accepted
date: 2026-08-31
summary: 将事件与策略经验建模为带证据的断言；候选图与正式图分离；用确定性缺口扫描驱动下一轮 ToG 探索。
related: ["ADR-007", "ADR-014", "ADR-018", "ADR-021"]
---

# ADR-023: Claim 一等公民、审核分层与缺口驱动探索


## 背景

Substance 将新闻存为 EVENT blob、影响存为 RELATION 边；同标的相反情绪进入 `conflict_group` 后激活按 `insertion_order` 丢弃反面。ToG `prune_paths` 由语言模型勾选 path id，假设树不再接线。`record_path_outcome` 已对策略 success/candidate 分档，但事件抽取直接进入默认可激活的正式图，采集原文不落盘。

结果是：Agent 读到的 \(G_t\) 混有未验证抽取；探索围着已有算子邻域展开，无法从「缺边 / 矛盾 / 未实验」生成下一任务。

## 决策

我们将知识图升级为可验证的外部认知状态，写入遵循 \(G_{t+1}=\mathrm{Validate}(G_t\oplus\Delta G_t^{\mathrm{Agent}})\)。

1. **Claim**：EVENT / STRATEGY 的 `metadata['claim']` 承载 subject / predicate / object / evidence_ref / valid_time。采集原文以 `review_status=raw` 落库；抽取事件用 `evidence_ref` 指向原文 sid。
2. **CONTRADICTS**：同冲突组正负情绪写 `relation_type=contradicts` 边，激活保留双方，禁止覆盖。
3. **审核分层**：`ReviewStatus` 为 raw / staging / committed。默认 `activate` 只注入 committed；RAW 永不激活且内容不可 `update`/`add` 覆盖。LLM 抽取与 `outcome=candidate` 为 staging；`success`/`failure`（已实验）为 committed。
4. **list_gaps**：确定性扫描持久化缺口与本次 beam / 训练集指纹，产出类型化任务清单。语言模型只选择先做哪条，不生成缺口本身。

工作流仍由 LangGraph 承担；知识图不与编排 DAG 合并。Validate 仍是回测 / OOS / `prove_causality`，不以 LLM Judge 替代。

## 后果

- **正面**：断言可回溯；矛盾可观测；未过门知识不再污染默认上下文；探索有缺口清单可执行。
- **负面**：新抽取事件默认不出现在 `prepare_context`；须 `include_staging=True` 或先过门。存量无 `review_status` 列视为 committed。
- **中性**：HypothesisTree 仍只作谱系存储；本 ADR 不复活 HTR 编排。
