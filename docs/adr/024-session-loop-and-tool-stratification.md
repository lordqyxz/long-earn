---
id: 24
title: 会话主循环与工具分层架构
status: Superseded
superseded_by: "dsh-long-earn-quant ADR-001（docs/adr/001-dsh-runtime-and-deterministic-engine-boundary.md）"
date: 2026-09-07
summary: MasterAgent 升级为会话式智能体套件；工具按 query_*/run_* 分层，事件与本体经查询工具接入主循环，长时任务异步隔离。
related: ["ADR-016", "ADR-021", "ADR-007", "ADR-014", "ADR-018"]
---

# ADR-024: 会话主循环与工具分层架构

> **处置（M5 划线，2026-10-06）：Superseded —— 会话主循环改由 DSH 运行时承担。**
> **为什么**：本 ADR 规划的五部件（会话主循环、上下文引擎、工具分层、子代理任务、底座读接口）与 DSH 已提供的会话管理、工具声明式注册、`ctx.jobs` 后台任务、subagents、技能库逐项重合；自建该基建的收益无法覆盖其上下文维护成本。新仓 ADR-001 §决策 1 据此改为整体借用 DSH，本仓库进入归档冻结，既有 LangGraph 编排层不再作为新功能的承载面（其退役节奏按规划源 M5 风险表执行——影子验证不达标则保留，**不预设退役时间点**）。
> **新实现在哪**：会话主循环与工具/子代理注册由 DSH 承担；分层语义落在新仓 `packages/plugin/src/index.ts`（`query_events` / `query_runs` / `query_symbols` / `query_task`）与 `packages/plugin/src/memory_graph.ts`（`query_memory`），长时工具 `run_research` 见 `packages/plugin/src/research_job.ts`（`ctx.jobs.start` + `agent.inject` 进度通知）、`run_memory_curate` 见 `packages/plugin/src/memory_curate_job.ts`；子代理模板见 `packages/plugin/src/research_subagent.ts`。
> **延续了什么**：`query_*`（只读、秒级、零语言模型调用）/ `run_*`（有状态、长时、返回任务句柄）的**命名即契约**分层不变；「长任务不阻塞主循环 + `query_task` 轮询句柄」「编排决策留在主智能体、子代理只作上下文隔离与工具可见性裁剪」「工具产出结构化两段式而非截断压平」三条语义均延续。
> **变了什么**：消息历史持久化由 LangGraph checkpointer 改为 DSH 会话自持；§B 上下文三机制（紧凑化 / 结构化笔记 / 按需加载）改由 DSH 运行时提供，本仓不再实现；子代理由 LangGraph 子图改为 DSH subagent；工具可见性裁剪由 DSH 声明式注册承担。

## 背景

ADR-016 将主图升级为 MasterAgent ReAct，六项任务工具均为子图薄封装，但智能体运行时仍是一次性调用。事实如下：

1. **无会话状态**：`MasterAgent.invoke(query)` 单发执行，消息历史不持久化；追问须整段重述上下文，跨调用仅剩 MemoryService 检索一条通道。
2. **工具契约弱**：六个工具全部返回拼接文本（部分含 `[:2000]` 截断兜底），子图的结构化结果经格式化函数压平后不可程序化消费；`summarize` 内置工具即为弥补文本整合而设。
3. **事件与本体游离于主循环**：`infer_events` 是一次性查询，事件虽持久化于 Substance（ADR-007），但会话无法渐进消费已积累的事件知识；Ontology Connector（ADR-014）仅为 ResearchAgent 与 stock_analysis 的内部依赖，主智能体无本体查询入口。能力存在但不可达。
4. **长任务同步阻塞**：`research_strategy` 内联执行 ResearchAgent（多轮探索与回测，分钟级），期间主循环无进度反馈、无并行、无取消。
5. **无工具可见性控制**：工具集静态固定，无法按阶段或按子任务裁剪。

对照 2025–2026 年智能体套件（agent harness）实践：Claude Code / DSH 具备会话持久化、工具声明式注册与可见性裁剪、后台任务与结构化工具输出；OpenAI Deep Research 与 Anthropic 多智能体研究系统采用计划-执行循环、Orchestrator-Worker 与并行工具调用，并以上下文压缩与结构化笔记维持长任务质量；Cognition 主张单一主智能体承担全部编排决策，子智能体仅作上下文隔离。本系统领域子图能力齐备，缺的是将它们组织为可用产品的会话运行时——既有能力与用户可感知的整机能之间存在系统性落差。

## 决策

我们将把 MasterAgent 升级为会话式智能体套件，由五个部分构成有机整体。

### A. 会话主循环

- 以 `thread_id` 标识会话；消息历史经 LangGraph checkpointer 持久化（起步 `MemorySaver`，后续接 PostgreSQL，ADR-019）。
- `invoke(query, thread_id)` 多轮复用；系统提示跨轮稳定。
- 会话结束或超预算时，将摘要写入 MemoryService（Substance 既有 form），实现跨会话沉淀。
- 主循环保持单智能体 ReAct 编排（ADR-016 不变）；不引入多主智能体骨架。

### B. 上下文引擎

上下文工程三机制：

1. **紧凑化（compaction）**：消息历史超过预算阈值时，旧消息折叠为摘要块；
2. **结构化笔记**：会话工作集以键值笔记维护（任务状态、已确认事实、标的清单、用户约束），每轮注入，介于压平历史与全文重读之间；
3. **按需加载**：事件、本体、行情事实不预载进上下文，由 `query_*` 工具按需取回（渐进披露：先摘要、后详情句柄）。

`prepare_context` 的确定性激活结果 `ContextActivation`（ADR-021）作为首轮笔记来源；miss 补采集经 `run_event_collection` 显式触发。

### C. 工具分层

工具按两组前缀划分，命名即契约：

| 组 | 契约 | 工具 |
|----|------|------|
| `query_*` | 只读、秒级、可并行、可在 ReAct 循环内高频调用；除 `web_search`（联网检索 Provider，ADR-021 审计豁免的基础设施能力）外零语言模型调用 | `query_events`（Substance 事件检索）、`query_ontology`（Connector.get_concept 薄封装）、`query_market`、`query_memory`、`query_task`、`web_search` |
| `run_*` | 有状态、长时（10 秒级以上）、含语言模型推理、返回任务句柄 | `run_research`（ResearchAgent，ADR-018）、`run_stock_analysis`（五视角子图）、`run_event_collection`（miss 采集子图，ADR-021） |

- 工具产出 typed dataclass，渲染为「摘要 + 结构化详情」两段；禁止截断式压平。
- 结构化输出落地后，`summarize` 内置工具退役（主模型直接消费结构化结果）；`infer_events` 拆分为 `query_events` 与 `run_event_collection`。

### D. 子代理任务

- `run_*` 返回 `TaskHandle(task_id, status, artifact_refs)`，主循环立即继续；子图在后台任务中执行，`query_task(task_id)` 查询进度与产物。
- 编排决策留在主智能体：子代理不嵌套派生子任务（Cognition 单一编排骨架原则）；其价值为 fresh context 隔离与工具可见性裁剪——按任务类型注入受限工具集。
- 失败语义结构化：失败原因与可重试标记随句柄返回，禁止静默吞异常；任务事件写入审计日志并关联 task_id。

### E. Substance 知识底座

- 不新造存储：Substance（ADR-007）是唯一事实底座，Ontology（ADR-014）是其图视图，事件推理管线（ADR-021）是底座的补写路径。
- `query_events` / `query_ontology` 是底座进入主循环的唯一读接口，`run_event_collection` 是显式写入口。
- 会话笔记与底座分层：笔记随 thread 生命周期存续（会话工作集），Substance 跨会话存续（PIT、decay、冲突消解）；会话经摘要沉淀入底座，不共享可变状态。

## 后果

**正面**

- 多轮对话可用；事件与本体进入主循环，「能力存在但不可达」消除；
- 长任务不阻塞主循环，`query_*` 可在 ReAct 循环内并行调用；
- 结构化工具契约提升主模型消费质量；与 ADR-021 分层天然兼容（`query_*` 零推理，`run_*` 即 agent 节点）。

**负面**

- 引入新基础设施：checkpointer、任务注册表与后台任务生命周期管理；
- 工具数量增至约九个，弱模型选择负担上升，须依赖分组命名与系统提示约束；
- 上下文三机制（紧凑化 / 笔记 / 按需加载）各自需要调参与测试，维护面扩大；
- 异步任务使调试与审计链路复杂化，审计日志须关联 task_id。

**中性**

- ADR-016 §A 工具表由本 ADR 修订（六个薄封装 → 两组前缀契约）；`summarize` 退役，`infer_events` 一分为二；
- 底座（§E）无新增实现量，是既有能力的接口化。

## 参考

- Anthropic, *Building Effective Agents*（2024-12）与 *How we built our multi-agent research system*（2025）
- OpenAI, *Deep Research*（2025）
- Cognition, *Don't Build Multi-Agents*（2025）
- DeepSeek Harness（DSH）工具插件模型：声明式注册、执行上下文与可见性控制
