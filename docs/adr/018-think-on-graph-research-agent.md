# ADR-018: Think-on-Graph 策略研发飞轮（HTR 控制器 → ResearchAgent）

日期: 2026-08
状态: Accepted

## 背景

ADR-016 将主图升级为 MasterAgent ReAct，但明确保留 ADR-010 的 HTR 六步循环为策略研发**唯一控制器**，并写明「策略研发不做全量 ReAct 化」。理由是量化 sharpe 噪声大，LLM 直觉不可靠。

后续实验表明该前提不成立：

1. **纯 HTR 研究子图**在真实运行中几乎产不出有效策略——固定步序限制了算子发现与假设探索的耦合。
2. **外置 ReAct Agent 直接调用回测 / 算子工具**能同时研发算子与策略，形成正向飞轮。
3. 事件图谱与 Substance 已具备图结构，但仅 stock_analysis 被动 `activate`，HTR 不消费；事件推理工具默认空 `CollectorRegistry`，基础设施未自动接入。
4. 数据层文档仍写「DuckDB→miniqmt→ciccwm→akshare 降级」，代码实际为单源 + 失败返回空——降级叙事制造假复杂度。

Think-on-Graph（ToG, ICLR 2024）与 ToG-2 提供了可迁移范式：LLM ⊗ KG——Agent 在知识图上逐步 explore / prune，证据充分后再作答；图检索与非结构化上下文交替。本项目将「作答」替换为「调用不可跳过的回测与统计门」。

本地论文与映射见 [docs/research/papers/](../research/papers/)。运行时总览图见 [docs/architecture.md](../architecture.md)。

## 决策

我们将策略研发的**探索控制器**从固定 HTR 状态机翻转为 **Think-on-Graph 风格的 ResearchAgent**；HTR 假设树与 ADR-015 统计门保留为**状态存储与证据硬约束**，不再作为编排铁律。

### A. ResearchAgent（LLM ⊗ Graph）

新建 `strategy_rd/research_agent.py`：`create_react_agent` + ToG 工具集。

| 类别 | 工具 | 职责 |
|------|------|------|
| 图 | `prepare_context` / `activate_subgraph` / `expand_relations` / `prune_paths` | 锚定实体、激活子图、扩展邻居、剪枝 beam |
| 研发 | `list_operators` / `develop_operator` / `compile_strategy_yaml` | 算子目录与策略 YAML |
| 证据（不可跳过） | `run_backtest` / `run_oos_gates` / `prove_causality` | 训练集回测、OOS/统计门、因果性 |
| 飞轮 | `record_path_outcome` | 路径结果写回 Substance |

Agent **可以**决定探索哪条假设路径；**不可以**跳过回测、OOS/DSR/PBO、三段式分割或算子因果证明——由工具实现与 AcceptanceGate 强制。

### B. MasterAgent 委托

`MasterAgent.research_strategy` 改为委托 `ResearchAgent.invoke`，不再直接 `create_htr_subgraph().invoke`。HTR 子图降级为兼容脚手架：内部节点（develop / AcceptanceGate）可被 ResearchAgent 工具复用，CLI/测试仍可调用 `create_htr_subgraph`。

### C. 事件图谱基础设施化

`RuntimeContext.prepare_context(query)`：

1. 尝试 `memory.activate_events`
2. 若结果为空且允许采集，用**默认注册**的 CollectorRegistry 跑轻量事件推理后再次 activate
3. 返回可注入 prompt 的上下文字符串

`create_event_inference_subgraph` 默认注册 Kimi + ciccwm collectors（可用时）。`Connector` 构造时注入 `memory_provider=memory`。

### D. 数据层取消降级叙事

- `CompositeDataConnector` 语义改为：**显式主源（miniqmt）+ DuckDB Cache**，失败即失败并打日志，不静默换源。
- ciccwm / akshare 作为**平行能力**（`MarketIntelligenceProvider` / 独立 Connector），由调用方或 Ontology 按 concept **点名**，不作 fallback。
- 实时行情：`CompositeRealtimeProvider` 改为「主源不可用时显式切换到已配置次源」，文档不再称「降级链」。

### E. 废弃路径清理

- `strategy_rd/subgraph.py`（已 DEPRECATED）归档删除；测试改为跳过或改挂 HTR / ResearchAgent。
- `stock_analysis` 的 `fund_flow_analysis` 补上从 `event_context` 的入边。

### F. 对 ADR-016 / ADR-010 的影响

- **Supersedes ADR-016 §C**「策略研发不做全量 ReAct 化」及「算子工具不暴露给主智能体之外的研究层」——算子工具归 ResearchAgent（策略飞轮层），MasterAgent 仍只暴露 `research_strategy`。
- **ADR-010**：假设树与合并门思想保留；六步顺序不再是唯一合法编排。
- **ADR-015 / 三段式 / prove_causality**：不变，仍为硬约束。
- **ADR-017**：前置条件中「ADR-016 主智能体落地」仍满足；策略基线改由 ResearchAgent 飞轮产出。

## 后果

### 正面

- 探索与证据解耦：LLM 负责搜索，引擎与统计门负责证明。
- 算子研发与策略研发回到同一 ReAct 循环，复现实验飞轮。
- 事件与记忆成为默认上下文，减少「记得调 infer_events」的编排负担。
- 数据层叙事与代码一致，降低运维误解。

### 负面

- ResearchAgent 工具数多于旧 `research_strategy` 单次 invoke，弱模型可能工具选择次优——需 ToG 风格 system prompt 与 beam 宽度上界。
- HTR 兼容路径与 ResearchAgent 短期双轨，增加维护面，需在 Spike 验证后收缩。
- 默认注册 collectors 可能引入外部 API 失败噪声——需 `is_available` 守卫与测试 skip。

### 中性

- 假设树文件格式可继续用于可视化探索路径。
- ToG-3 MACER 多 Agent 本轮不落地。
