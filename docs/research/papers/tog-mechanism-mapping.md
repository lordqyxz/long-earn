# ToG 机制 → Long Earn 模块映射

> 依据：ToG (arXiv:2307.07697) + ToG-2 (arXiv:2407.10805)  
> 决策：ADR-018

## 1. 范式对照

| ToG / ToG-2 | Long Earn |
|-------------|-----------|
| LLM ⊗ KG（逐步在图上思考，非一次 RAG） | `ResearchAgent` ReAct 循环 + Substance / Ontology 图工具 |
| Beam search explore + prune | 假设路径 beam（`expand_relations` → `prune_paths`），假设树作谱系存储 |
| 证据充分性判定后作答 | 证据充分 → 调 `run_backtest` / `run_oos_gates`（不可跳过） |
| ToG-2：图检索 ↔ 文档上下文交替 | 结构化边（Ontology / RELATION / 算子依赖）↔ 非结构化（经验、事件叙述） |
| Knowledge traceability | `record_path_outcome` 写回 Substance，路径可追溯 |
| Training-free plug-and-play | 不微调 LLM；工具契约 + 硬门固定 |

## 2. 工具映射

| 论文动作 | 工具 | 模块 |
|----------|------|------|
| Topic entity linking / 锚定实体 | `prepare_context` / `activate_subgraph` | `context_init` / `MemoryService.activate_events` |
| Explore neighbors on KG | `expand_relations` | `OntologyGraph.traverse` + Substance RELATION |
| Prune beam paths | `prune_paths` | ResearchAgent 内 LLM 决策 + 路径状态 |
| Retrieve unstructured context | `retrieve_memory` / 事件叙述 | `MemoryService.search` / `activate_events` |
| Domain action（本域扩展） | `list_operators` / `develop_operator` / `compile_strategy_yaml` | ADR-009 算子目录 + operator_dev |
| Evidence（本域扩展） | `run_backtest` / `run_oos_gates` / `prove_causality` | backtest + ADR-015 |
| Write back knowledge | `record_path_outcome` | `MemoryService.save_experience` / Substance |

## 3. 硬约束（论文未覆盖，本项目铁律）

Agent **可以**决定探索哪条路，**不可以**：

- 跳过回测或 OOS / DSR / PBO
- 跳过 `prove_causality` 上线算子
- 交叉使用训练 / 测试 / 验证集

这些由工具实现与 AcceptanceGate 强制，而非 prompt 约定。

## 4. 与旧 HTR 的关系

| 保留 | 降级 / 废弃 |
|------|-------------|
| 假设树作探索状态与谱系 | 固定 `observe→ideate→…` 状态机作唯一控制器 |
| OOS 合并门 + 三道统计门 | 「策略研发不做全量 ReAct」（ADR-016 §C） |
| 算子缺口可触发研发 | 仅 executor 内有限逃生口 |
