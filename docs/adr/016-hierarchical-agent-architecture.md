# ADR-016: 分层智能体架构（主智能体 ReAct + 算子缺口闭环）

日期: 2026-07
状态: Accepted（§C 策略研发编排条款 **Superseded by [ADR-018](018-think-on-graph-research-agent.md)**：探索控制器改为 ResearchAgent；MasterAgent 仍负责任务分解）

## 背景

原主图是「带 LLM 路由器的三子图直通」：意图分析在三个固定子图中选一个直通--是路由器不是智能体。缺陷：无任务分解（「分析茅台并给我策略」不可处理）；子图产出对主图不透明（整个 dict 塞进状态）；operator_dev 已能自研算子但未同步接入策略研发流（develop 因算子缺失失败即中断，无法同流程补齐再重试）。

## 决策

主图升级为**分层智能体架构**：MasterAgent（ReAct）负责任务分解与跨子图编排，子图负责领域深度。

### A. MasterAgent（`master_agent.py`）

`create_react_agent` + 6 个任务工具（子图/服务的薄封装，不重写领域逻辑）：

| 工具 | 承接 |
|------|------|
| `research_strategy(idea, constraints)` | 策略研发（ADR-018 后委托 ResearchAgent），返回最佳策略 YAML + 指标 + 探索路径摘要 |
| `analyze_stock(query, symbols)` | 股票分析子图（5 视角并行） |
| `infer_events(query)` | 事件推理子图 |
| `retrieve_memory(query, k)` | MemoryService |
| `web_search(query)` | kimi_web_search |
| `summarize(...)` | 内置，整合多工具结果 |

System prompt 要点：复合请求先分解再调度；基于工具返回的结构化证据回答，不编造。

### B. 算子缺口闭环下沉（仍有效）

算子研发工具（`list_operators` / `detect_operator_gap` / `develop_operator`）下沉到策略研发层内部（现 ResearchAgent 工具），**不暴露给 MasterAgent**：

1. **避免抽象泄漏**：主智能体不应理解算子目录/OperatorSpec/DSL 领域概念，否则工具选择质量下降；
2. **时序正确性**：算子研发是策略研发流内的同步子任务（develop 失败 -> 同一循环内研发算子 -> 重试，或 prune 该假设方向）；上提到主智能体会死锁或时序错乱（研发流已中断）；
3. **闭环完整**：研发成功重试 / 失败 prune 都在研发层工具集内。`develop_operator` 内部强制 `prove_causality`，自主注册算子带 reference_strategy + motivation 可回溯到触发 run。

### C. 策略研发编排（§C，已被 ADR-018 Supersede）

原决策「保留 HTR 六步骨架为唯一编排、不做全量 ReAct 化」废止：实验表明纯 HTR 子图产不出有效策略，外置 ReAct + 回测/算子工具形成飞轮。HTR 假设树与统计门保留为状态与硬约束。原逃生口设计保留于 `escape_hatch.py`（算子缺口检测 -> 同步研发 -> 重试；executor 失败分类 -> 可修复走 refine / 方向性失败直接 prune），作为 ResearchAgent 工具内部实现参考。

### D. 防过拟合硬约束（铁律不降级）

| 约束 | 实施位置 | LLM 可否跳过 |
|------|---------|-------------|
| 三段式数据分割 | 回测 / OOS 节点与工具内部（config 区间强制） | 否 |
| OOS 合并门 + 三道统计门（ADR-015） | 合并门内部串行追加 | 否 |
| PIT 对齐 | 数据层 VisibilityGuard | 否 |
| 算子目录白名单 + 因果证明 | DSL 解析期 / develop_operator 内部 | 否 |
| 搜索策略（分支数/展开深度/剪枝时机） | decide / Agent 决策 | 软（配置硬上界内自主，树状态强制记录） |

**分界原则**：证据相关约束（回测、OOS 验证、步序因果）不可跳过；资源分配决策在硬上界内可自主。

## 后果

- 复合请求可处理；算子缺口在研发流内同步闭环；工具集薄封装迁移风险低。
- ReAct 循环 LLM 调用次数与 token 成本上升；分层调试复杂度增加（主智能体 <-> 子图 <-> executor 三层），需工具调用审计日志；弱模型工具选择需好的 system prompt 与 few-shot。
- ADR-010 保持 Accepted（Enhanced）；自我进化拆出 ADR-017（Deferred）。
