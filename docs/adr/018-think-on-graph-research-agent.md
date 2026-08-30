---
id: 18
title: Think-on-Graph 策略研发正反馈闭环
status: Accepted
date: 2026-08
summary: 探索控制器移交 ResearchAgent；回测与统计验证门控为不可跳过的证据链。
amended_by: ["ADR-021"]
related: ["ADR-010", "ADR-016", "ADR-022"]
---


# ADR-018: Think-on-Graph 策略研发正反馈闭环


## 背景

ADR-016 将主图升级为 MasterAgent ReAct，但明确保留 ADR-010 的 HTR 六步循环为策略研发唯一控制器，并规定「策略研发不做全量 ReAct 化」，理由是量化夏普噪声大、语言模型直觉不可靠。

后续实验表明该前提不成立：

1. 纯 HTR 研究子图在真实运行中几乎产不出有效策略——固定步序限制了算子发现与假设探索的耦合；
2. 外置 ReAct Agent 直接调用回测与算子工具，能同时研发算子与策略，形成正反馈闭环；
3. 事件图谱与 Substance 已具备图结构，但仅 stock_analysis 被动 `activate`，HTR 不消费；事件推理工具默认空 `CollectorRegistry`，基础设施未自动接入；
4. 数据层文档仍写「DuckDB→miniqmt→ciccwm→akshare 跨源切换」，代码实际为单源加失败返回空——该表述制造虚假复杂度。

Think-on-Graph（ToG, ICLR 2024）与 ToG-2 提供可迁移范式：语言模型与知识图交替检索与非结构化上下文，在图上逐步探索与剪枝，证据充分后再作答。本项目将「作答」替换为「调用不可跳过的回测与统计门控」。

本地论文与映射见 [docs/research/papers/](../research/papers/)；运行时总览见 [docs/architecture.md](../architecture.md)。

## 决策

我们将策略研发的探索控制器从固定 HTR 状态机翻转为 Think-on-Graph 风格的 ResearchAgent；HTR 假设树与 ADR-015 统计反馈保留为状态存储与证据硬性约束，不再作为编排硬性约束。

### A. ResearchAgent（语言模型 ⊗ 图）

新建 `strategy_rd/research_agent.py`：基于 `create_react_agent` 与 ToG 工具集。

| 类别 | 工具 | 职责 |
|------|------|------|
| 图 | `prepare_context` / `activate_subgraph` / `expand_relations` / `prune_paths` | 锚定实体、激活子图、扩展邻居、剪枝 beam |
| 研发 | `list_operators` / `develop_operator` / `compile_strategy_yaml` | 算子目录与策略 YAML |
| 证据（不可跳过） | `run_backtest` / `run_oos_gates` / `prove_causality` | 训练集回测、OOS/统计门、因果性 |
| 写回 | `record_path_outcome` | 路径结果写回 Substance |

Agent 可决定探索哪条假设路径；不可跳过回测、OOS/DSR/PBO、三段式分割或算子因果证明——由工具实现与 AcceptanceGate 强制。

### B. MasterAgent 委托

`MasterAgent.research_strategy` 改为委托 `ResearchAgent.invoke`，不再直接 `create_htr_subgraph().invoke`。HTR 子图曾降为兼容脚手架（内部节点 develop、AcceptanceGate 可被工具复用）。ADR-010 已 Deprecated（2026-08-30）：HTR 编排已删除；入口为 ResearchAgent；不得再新增对遗留编排路径的依赖。

### C. 事件图谱基础设施化（已由 ADR-021 修订）

原决策将「缺失 → 默认 CollectorRegistry 运行事件推理 → 再 activate」嵌在 `prepare_context` 内部。ADR-021 修订触发点，不改变「研究入口缺失时自动补采集」的行为语义：

1. `RuntimeContext.prepare_context(query)`（及底层 `ContextPreparationService`）仅做确定性激活，返回结构化 `ContextActivation`（含 `missed` 等标记），不内嵌语言模型；
2. 缺失时的采集推理由调用方在 agent 层显式触发：ResearchAgent 入口、`prepare_context` 工具闭包、app 事件管线各自构造并调用事件推理子图，再二次 activate；
3. `create_event_inference_subgraph` 默认注册 Kimi 与 ciccwm collectors（可用时）；`Connector` 构造时注入 `memory_provider=memory`。

现行契约以 ADR-021 为准；本条保留「事件成为默认上下文」的意图与入口统一。

### D. 数据层取消跨源静默换源表述

- `CompositeDataConnector` 语义改为：显式主源（miniqmt）加 Cache，失败即失败并记录日志，禁止静默跨源换源（不得 miniqmt → ciccwm → akshare 自动切换）。（Cache 后端在决策时为 DuckDB；ADR-019 已统一迁移至 PostgreSQL。）
- ciccwm、akshare 作为平行能力（`MarketIntelligenceProvider`、独立 Connector），由调用方或 Ontology 按 concept 显式点名，不作备用数据源；
- 实时行情：`CompositeRealtimeProvider` 改为主源不可用时显式切换到已配置次源，文档不再称「降级链」；
- **术语边界（与 ADR-020）**：本条禁止跨源静默换源。同一 PostgreSQL Cache 内「宽表快路径与即时合并路径」的同源算法备用读路径（ADR-020）不在禁止之列——属读路径实现细节，非数据源切换。

### E. 废弃路径清理

- `strategy_rd/subgraph.py`（已 DEPRECATED）归档删除；测试改为跳过或改挂 HTR / ResearchAgent；
- `stock_analysis` 的 `fund_flow_analysis` 补上从 `event_context` 的入边。

### F. 对关联 ADR 的影响

- Supersedes ADR-016 §C「策略研发不做全量 ReAct 化」——算子工具归 ResearchAgent，MasterAgent 仍只暴露 `research_strategy`；
- ADR-010：Deprecated——假设树状态与合并门思想保留（门由 ADR-015 / ResearchAgent 消费）；六步编排实现已删除；
- ADR-015、三段式、`prove_causality`：反馈闭环（015 Tier A/B）与三段式、因果证明不变；统计门用法见 ADR-022；
- ADR-017：技能规格仍 Deferred；解锁节奏改由 ADR-022；策略基线改由 ResearchAgent 迭代产出。

## 后果

**正面**

- 探索与证据解耦：语言模型负责搜索，引擎与统计门负责证明；
- 算子研发与策略研发回到同一 ReAct 循环，复现实验迭代闭环；
- 事件与记忆成为默认上下文，减少须显式调用 `infer_events` 的编排负担；
- 数据层文档表述与代码一致，降低运维误解。

**负面**

- ResearchAgent 工具数多于旧 `research_strategy` 单次 invoke，弱模型可能工具选择次优，须配合 ToG 风格系统提示与 beam 宽度上界；
- HTR 兼容路径已删除（ADR-010 Deprecated）；CLI / API 已迁至 ResearchAgent；
- 默认注册 collectors 可能引入外部 API 失败噪声，须 `is_available` 守卫与测试 skip。

**中性**

- 假设树文件格式可继续用于可视化探索路径；
- ToG-3 MACER 多 Agent 本轮不实现。
