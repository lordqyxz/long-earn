# ADR-016: 分层智能体架构（主智能体 ReAct + 算子缺口闭环下沉）

日期: 2026-07
状态: Accepted（§C 策略研发编排条款 **Superseded by [ADR-018](018-think-on-graph-research-agent.md)**）

## 背景

当前系统是"带 LLM 路由器的三子图直通"架构（`agent.py`，已在阶段 5 删除）：

```
START → intent_analyze(LLM 路由) → {strategy_rd | stock_analysis | event_inference} → summarize → END
```

意图分析节点用 LLM 在三个固定子图中选一个，然后 `subgraph.invoke({"query": ...})` 直通，子图之间无协作、无任务分解、无结果反思。这是**路由器**，不是**智能体**。

具体缺陷：

1. **无任务分解能力**：用户说"分析茅台并给我一个适合它的策略"这种复合请求，当前路由器只能选一个子图，无法让策略研发与股票分析协作。
2. **子图产出对主图不透明**：主图 `_strategy_rd_node` 只把 `subgraph.invoke` 的整个结果 dict 塞进 `strategy_result`，不结构化抽取关键产物，下游 summarize 节点拿到的也是一坨。
3. **算子研发未同步接入策略研发流**：`operator_dev` 子图已能自主研发算子（log_return / realized_vol 就是产物），但 `gap_detector` 只在 HTR 子图内部以固定节点接线，LLM 无法根据中间结果自主决定"现在值得停下研发一个新算子吗"。当 `develop_strategy` 因算子缺失失败时，策略研发流被中断，无法在同一流程内补齐算子再重试。

ADR-010 的 HTR 六步循环 + ADR-015 的三道统计门已构成策略研发的可信骨架。本 ADR **不替换该骨架**，而是解决主图层的智能体能力缺失与算子闭环的同步接入问题。

## 决策

将主图升级为**分层智能体架构**：主智能体用 ReAct 自主调度任务分解与跨子图协作；策略研发子图保留 ADR-010 六步骨架为默认编排，仅在算子缺口与 executor 失败等特定点引入有限逃生口；算子研发工具下沉到策略研发流内部，实现"假设驱动算子研发"的同步闭环。

### A. 两层架构

```
┌─────────────────────────────────────────────────────────────┐
│  主智能体 (MasterAgent, ReAct)                               │
│  职责：意图理解 → 任务分解 → 工具调度 → 结果整合 → 回答生成    │
│  工具集：research_strategy / analyze_stock / infer_events    │
│         / retrieve_memory / web_search / summarize          │
└────────┬────────────────┬───────────────────┬───────────────┘
         │                │                   │
         ▼                ▼                   ▼
┌────────────────┐ ┌────────────────┐ ┌────────────────┐
│ 策略研发子图    │ │ 股票分析子图    │ │ 事件推理子图    │
│ (HTR 六步循环   │ │ (5 视角并行)   │ │ (现有子图)     │
│  + 有限逃生口)  │ │                │ │                │
│                │ │                │ │                │
│ 状态：假设树    │ │ 状态：股票结论  │ │ 状态：事件图谱  │
│ 硬约束：OOS门  │ │                │ │                │
│ /三段式/PIT    │ │                │ │                │
│ 逃生口：算子    │ │                │ │                │
│  研发同步接入  │ │                │ │                │
└────────────────┘ └────────────────┘ └────────────────┘
```

主智能体负责编排（任务分解、跨子图协作、结果整合），子图负责领域深度。策略研发子图保留 ADR-010 六步循环 + ADR-015 统计门为可信骨架，不做全量 ReAct 化。

### B. 主智能体（MasterAgent）

用 `langgraph.prebuilt.create_react_agent` 实现，LLM 作为控制器自主选择工具。

**工具集**（每个工具是一个子图或基础能力的薄封装）：

| 工具 | 承接 | 说明 |
|------|------|------|
| `research_strategy(idea, constraints)` | 策略研发子图 | 委托 HTR 子图，返回最佳策略 YAML + 回测指标 + 探索路径摘要 |
| `analyze_stock(query, symbols)` | 股票分析子图 | 委托 5 视角并行子图，返回多视角结论 |
| `infer_events(query)` | 事件推理子图 | 委托现有 `create_event_inference_subgraph` |
| `retrieve_memory(query, k)` | MemoryService | 检索历史策略经验/知识 |
| `web_search(query)` | kimi_web_search 工具 | 实时信息补充 |
| `summarize(...)` | 内置 | 整合多工具结果为客户友好回复 |

**System Prompt 要点**：
- 你是 Long Earn 量化交易顾问智能体，负责理解用户意图、分解任务、调度专业子图、整合结果。
- 可调用工具见列表。对复合请求，先分解为子任务，并行/串行调用合适工具，最后整合。
- 策略研发类请求 → `research_strategy`；单股票/公司分析 → `analyze_stock`；新闻事件驱动 → `infer_events`；混合请求按需组合。
- 工具返回的是结构化结果，你需要基于证据生成客户友好回复，不要编造工具未返回的数据。

**状态契约**（主智能体 Messages State）：
- `messages: list` — ReAct 对话历史（含工具调用与返回）
- `user_query: str` — 原始用户查询
- `tool_results: dict[str, Any]` — 各工具返回的结构化结果（供 summarize 引用）
- `final_answer: str` — 最终客户回复

### C. 策略研发——六步骨架保留 + 有限逃生口

> **[ADR-018](018-think-on-graph-research-agent.md) 修订**：实验表明纯 HTR 子图难以产出有效策略，而外置 ReAct + 回测/算子工具能形成飞轮。策略研发的**探索控制器**改为 ToG 风格 `ResearchAgent`；本节「不做全量 ReAct 化」条款废止。HTR 假设树与 ADR-015 统计门仍为状态存储与证据硬约束。下文逃生口设计仍可作为 ResearchAgent 工具内部实现参考。

**原决策（已废止编排地位）**：保留 ADR-010 的六步循环为默认编排（`observe→ideate→select→dispatch→executor→backpropagate→decide`），**不做全量 ReAct 化**。理由曾是：ADR-015 的实证表明，量化 sharpe 噪声大，LLM 直觉不可靠——把研究策略决策权完全交给 LLM 自主，不比固定 workflow + 统计门更优。

**在六步骨架的特定节点引入有限逃生口**——LLM 在这些点做局部决策，但不改变整体步序：

**逃生口 1：算子缺口（executor 内）**

`develop_strategy` 返回 `{error: "operator_not_found", missing: ["alpha_decay"]}` 时，LLM 自主决策：

```
子图 executor 内 LLM 思考步：
  "这个假设需要 alpha_decay 算子，目录里没有，值得研发吗？"
  ↓ 值得 → 调 list_operators() 确认确实缺
  ↓ 调 detect_operator_gap(yaml, intent) 产出 OperatorSpec
  ↓ 调 develop_operator(spec)
     ├ 成功 → 调 develop_strategy(hypothesis) 重试
     └ 失败 → 标记该假设为 pruned，backpropagate 失败原因
  ↓ 不值得 → 标记 pruned，进入 backpropagate
```

此逃生口在 executor 内部闭环，**不中断六步循环的主流程**。`backpropagate` 收到失败原因后照常向上传播洞察，`decide` 照常决策下一步。

**逃生口 2：executor 失败路径选择（executor 内）**

executor 回测失败时，当前固定走 `refine` 循环（最多 3 轮）。引入逃生口后，LLM 可根据失败类型决定：

- **可修复错误**（YAML 语法、参数范围）→ 走 refine（现有行为）
- **方向性失败**（假设本身不合理、因子在训练集无信号）→ 直接标记 pruned，跳过 refine，进入 backpropagate 传播失败原因

**搜索策略决策（decide 节点保留）**：

`decide` 节点的 `expand`/`prune` 决策保留——这是搜索策略决策（资源分配），不是证据决策。无论展开 1 个还是 5 个分支，每个分支都必须走完整回测 + OOS 验证。LLM 可在配置硬上界（`HTR_MAX_CYCLES` / `HTR_MAX_DEPTH` / `HTR_BRANCHING_FACTOR`）内自主调整实际分支数与展开深度，但不可突破上界。

**不做的事**（明确排除，均为证据/步序相关）：

- ❌ LLM 自主决定跳过回测（"这个假设看起来合理，不 backtest 了"）——回测是证据来源，LLM 直觉不可替代
- ❌ LLM 自主决定跳过 OOS 验证——合并门是铁律
- ❌ LLM 自主决定步序（"先 ideate 再 observe"）——六步顺序保证 observe→ideate 的因果链

### D. 算子缺口闭环下沉到策略研发流

**设计原则（工具下沉，避免抽象泄漏）**：

算子研发工具下沉到**策略研发子图（executor 节点内）**，**不暴露给主智能体**。主智能体完全不感知"算子"这一领域概念——它只调用 `research_strategy`，子图内部自主处理算子缺口。

| 工具 | 所在层 | 行为 |
|------|--------|------|
| `detect_operator_gap(strategy_yaml, improvement_intent)` | 策略研发子图 executor | 扫描策略 YAML 与改进意图，对比算子目录，产出 `OperatorSpec` 写入 `OperatorBacklog`（复用现有 `_gap_detector_node` 逻辑） |
| `develop_operator(spec)` | 策略研发子图 executor | 委托 `create_operator_dev_subgraph` 跑 spec→审计→因果证明→register 闭环（复用现有子图） |
| `list_operators()` | 策略研发子图 executor | 返回当前算子目录，供 executor LLM 判断"我要的算子是否存在" |

**为什么下沉而非上提**：

1. **避免抽象泄漏**：主智能体的职责是任务分解与跨子图编排，不应理解算子目录、OperatorSpec、DSL 这些策略研发内部概念。若主智能体持有算子工具，每次决策都需理解领域细节，工具选择质量下降。
2. **时序正确性**：算子研发是策略研发流中的**同步子任务**——`develop_strategy` 因算子缺失失败后，需在**同一 executor 循环内**研发算子并重试，而非返回主智能体事后处理。若工具在主智能体层，子图遇到算子缺失要么卡住（死锁）、要么把失败上抛让主智能体事后研发（时序错乱，策略研发流已中断）。
3. **闭环完整**：研发成功后重试 `develop_strategy`、研发失败则 prune 该假设方向——这些后续动作都在策略研发子图的工具集内，闭环自然。主智能体介入只会引入跨层往返。

**硬约束保留**：`develop_operator` 委托的 `create_operator_dev_subgraph` 内部强制 `prove_causality` 因果性证明——含未来函数的算子绝不进目录（ADR-009）。

**版本追溯**：自主注册的算子在算子类 docstring 标注"operator_dev 自主研发产物"（现有 log_return / realized_vol 已是此模式），`OperatorSpec` 的 `reference_strategy` + `motivation` 字段记录触发它的任务上下文与假设 ID，可回溯到具体策略研发 run。

### E. 股票分析子智能体

现有 5 视角并行子图已较成熟，暂保持现有子图形态作为主智能体的 `analyze_stock` 工具被调用。后续可视需要升级为 ReAct 包装（LLM 自主决定调用哪些视角），但非本 ADR 强制范围。

### F. 事件推理子智能体

现有 `create_event_inference_subgraph` 暂保持子图形态，作为主智能体的 `infer_events` 工具被调用。

### G. 防过拟合硬约束（铁律不降级）

| 约束 | 实施位置 | LLM 可否跳过 |
|------|---------|-------------|
| 三段式数据分割 | HTR 子图 `backtest` / `validate_oos` 节点内部 | 否，节点强制使用 config 区间 |
| OOS 合并门 | HTR 子图 `_evaluate_oos_and_merge` 节点内部 | 否，节点内部校验 oos_score |
| 三道统计门（ADR-015） | `WalkForwardStabilityGate` / `DeflatedSharpeGate` / `BacktestOverfitGate` | 否，在合并门内部追加执行 |
| PIT 对齐 | 数据层（ADR-005 VisibilityGuard） | 否，数据层保证 |
| 算子目录白名单 | `develop_strategy` / DSL 解析期 | 否，DSL 解析期拒绝退役语法 |
| 算子因果性证明 | `develop_operator` → operator_dev 子图 `test_validate` | 否，`prove_causality` 硬约束 |
| 搜索策略（expand/prune/分支数） | decide 节点 LLM 决策 | 软决策（配置硬上界内自主，树状态强制记录） |
| 家族失效检测 | `ideate` 节点内部注入 family_state | 软提示（LLM 收到家族失效信号后自主决定是否切换） |

**关键约束**：证据相关约束（回测、OOS 验证、步序）不可跳过；搜索策略决策（分支数、展开深度、剪枝时机）在配置硬上界内由 `decide` 节点 LLM 自主。

### H. 与 ADR-010 的关系

本 ADR **Enhances ADR-010**，不 Supersede：

- **保留**：`HypothesisTree` / `HypothesisNode` 领域实体、`HypothesisTreeStore` 持久化、OOS 合并门、家族失效检测、洞察传播、六步循环编排
- **增强**：executor 节点引入算子缺口逃生口与失败路径选择逃生口
- **不替换**：固定六步循环 → 仍是默认编排；`decide` 节点的 `{merge, continue, expand, prune, stop}` 决策逻辑保留
- **保留**：`decide_prompt.md` 的决策逻辑不变
- **保留**：ADR-015 的三道统计门在合并门内部强制执行

ADR-010 状态从 "Superseded by ADR-016" 恢复为 "Accepted（Enhanced by ADR-015 + ADR-016）"。

### I. Prompt 优化融入本升级

之前实测发现的 6 项 prompt 问题在本升级中统一修复：

1. `strategy_optimize_prompt` 外迁 .md（已落地 `strategy_optimize_prompt.md`）+ 修复 ADR-009 退役语法（已落地）
2. `strategy_research_prompt.md` 字段表与算子目录同步 → 在 `develop_strategy` 节点 prompt 中统一
3. `ideate_prompt.md` 增加可用字段约束 → 在 `ideate` 节点 prompt 中加入
4. `supervisor_continue_prompt.md` frontmatter 截断 → 由 ADR-015 的统计门反馈替代（stagnation 信号直接注入 decide prompt）
5. few-shot 示例日期 2020-2023 → 同步到 2022-2024（训练集实际区间）
6. `strategy_research_prompt.md` 的 JSON Schema 输出契约与 agent 行为不一致 → 升级后 `research_strategy` 工具直接返回结构化 dict，不再走"LLM 返回 JSON 字符串塞进 description"的反模式

## 理由

1. **主智能体 ReAct 是最高杠杆点**：当前主图只是路由器，升级后具备任务分解、跨子图协作、结果反思能力——这是"智能体"与"路由器"的本质差别。复合请求（"分析茅台并给我策略"）从不可处理变为可处理。
2. **六步骨架保留而非替换**：ADR-015 的实证（Q1 -5.48% / Q2 +20.09% 的过拟合策略通过了旧 OOS 门）证明，量化 sharpe 噪声大，LLM 直觉不可靠。把研究策略决策权完全交给 LLM 自主，不比固定 workflow + 统计门更优。保留六步骨架 + ADR-015 统计门为可信骨架，仅在 executor 内引入有限逃生口解决算子缺口与失败路径问题——这是"证据驱动"原则的延续。
3. **算子闭环下沉解决真实断点**：当前 `operator_dev` 子图虽已实现但未同步接入策略研发流——`develop_strategy` 因算子缺失失败时策略研发流中断。逃生口 1 在 executor 内闭环补齐算子再重试，结束"独立模块未入主图"状态，实现"假设驱动算子研发"的同步接入。
4. **分层而非扁平**：策略研发、股票分析、事件推理是深度不同的领域，各自内部逻辑复杂度差异大。主智能体负责编排，子图负责领域深度，避免单个 ReAct 循环工具过多导致 LLM 决策质量下降。
5. **工具集薄封装**：主智能体工具集是对现有子图 invoke 的薄封装，不重写领域逻辑，降低迁移风险。
6. **自我进化拆为独立 ADR**：自我进化能力（经验回写、元指标、失败反思、prompt 自审）体量大、与"找最优策略"的因果链间接、且依赖系统先产出稳健策略作为基线。拆为 ADR-017 独立评审，推迟到 ADR-015 统计门端到端验证产出稳健策略之后再启动。

## 后果

**正面**：
- 主智能体具备任务分解与跨子图协作能力，复合请求可处理
- 算子缺口在策略研发流内同步闭环，不再中断研发流
- 保留六步骨架与 ADR-015 统计门，防过拟合能力不降级
- prompt 体系在升级中统一修复，消除 ADR-009 红线违规与契约不一致
- 工具集薄封装，迁移风险可控

**负面**：
- 主智能体 ReAct 循环的 LLM 调用次数增加（每次工具选择都是一次 LLM 调用），总 token 成本上升
- ReAct 的决策质量依赖 LLM 能力，弱模型可能在工具选择上次优——需配套好的 system prompt 与 few-shot
- 分层架构增加调试复杂度（主智能体 ↔ 子图 ↔ executor 逃生口三层），需完善工具调用审计日志
- `create_react_agent` 的状态管理与传统 StateGraph 不同，团队需熟悉 ReAct 模式
- executor 逃生口引入局部 LLM 决策，需审计日志确保不偏离"证据驱动"原则

**中性**：
- ADR-010 恢复为 Accepted（Enhanced by ADR-015 + ADR-016），不是被推翻而是被增强
- 自我进化能力拆为 ADR-017，独立评审与排期

## 分阶段实施计划

> 逐阶段交付，每阶段含 Serena 诊断 + ruff + lint-imports + pytest 单元测试。

### 阶段 1：主智能体（MasterAgent）+ 工具集薄封装

**状态：已完成** — `MasterAgent` 已实现并替代旧 `agent.py`，785 单元测试全绿。

**目标**：用 `create_react_agent` 实现主智能体，三个子图暂作为工具被调用（子图内部不改造）。

- 新增 `src/long_earn/master_agent.py`：`MasterAgent` 类，`create_react_agent` + system prompt + 6 个任务工具
- 任务工具 `research_strategy` / `analyze_stock` / `infer_events` 内部委托现有子图 invoke
- 任务工具 `retrieve_memory` / `web_search` / `summarize` 薄封装现有服务
- 修改 `__main__.py` / `cli.py`：`agent` 命令调用 `MasterAgent` 替代 `create_main_agent`
- 旧 `agent.py` 保留供回滚，标记 DEPRECATED
- 测试：`tests/unit/test_master_agent.py`（工具集契约 + ReAct 编译 + 路由决策）

### 阶段 2：策略研发 executor 算子缺口逃生口

**状态：已完成** — `escape_hatch.py` 模块实现算子缺口检测 + 同步研发 + 重试闭环，32 单元测试全绿。

**目标**：在 HTR executor 节点内引入算子研发同步闭环，不改变六步骨架。

- executor 内 `develop_strategy` 失败时，新增局部 LLM 决策步：是否研发算子
- 新增 `list_operators` / `detect_operator_gap` / `develop_operator` 作为 executor 内部工具（委托现有 `_gap_detector_node` 逻辑 + `create_operator_dev_subgraph`）
- 研发成功 → 重试 `develop_strategy`；研发失败 → 标记 pruned + backpropagate 失败原因
- 硬约束保留：`develop_operator` 内部强制 `prove_causality`
- 新增 executor 决策审计日志（记录 LLM 的算子研发决策与理由）
- 测试：`tests/unit/test_executor_escape_hatch.py`（算子缺口触发 → 研发 → 重试 / 研发失败 → prune）

### 阶段 3：策略研发 executor 失败路径逃生口

**状态：已完成** — `classify_failure_type` + `escape_hatch_failure_path` 实现 LLM 分类 + refine/prune 选择，10 单元测试全绿。

**目标**：executor 回测失败时，LLM 可选择 refine 或直接 prune。

- executor 回测失败时，新增局部 LLM 决策步：失败类型判断
  - 可修复错误（YAML 语法、参数范围）→ 走 refine（现有行为）
  - 方向性失败（假设不合理、训练集无信号）→ 直接 pruned + backpropagate 失败原因
- 失败路径决策审计日志
- 测试：`tests/unit/test_executor_failure_path.py`（可修复 → refine / 方向性 → prune）

### 阶段 4：Prompt 体系统一修复

**状态：已完成** — 字段表统一、日期同步、JSON Schema 简化、CI grep 卡口均已落地。

**目标**：在升级后的节点 prompt 中统一修复实测发现的 6 项问题。

- `develop_strategy` 节点 prompt 统一字段表与算子目录（同步 `strategy_develop_prompt.md`）
- `ideate` 节点 prompt 增加可用字段约束
- 所有 few-shot 示例日期同步到 2022-2024
- `strategy_research_prompt.md` 的 JSON Schema 契约废弃，工具直接返回结构化 dict
- CI grep 卡口防止退役语法回退

### 阶段 5：旧代码清理 + 文档收尾

**状态：已完成** — `agent.py` 及其测试已删除，ADR 状态更新为 Accepted。

**目标**：移除被替换的旧代码，更新文档。

- 删除 `agent.py`（被 `master_agent.py` 替代）
- 更新 `AGENTS.md` 模块结构说明
- 更新 `docs/adr/README.md` 索引

## 与其他 ADR 的关系

- **ADR-002**（partial 节点注入）：主智能体 ReAct 工具集不再用 partial 注入，但子图内部仍沿用
- **ADR-007**（物质-运动架构）：`retrieve_memory` 工具委托 MemoryService，复用双通道检索
- **ADR-009**（算子目录）：`develop_strategy` 节点内部强制走算子目录 DSL。`detect_operator_gap` / `develop_operator` / `list_operators` 工具**下沉到策略研发 executor**（非主智能体），把 operator_dev 子图同步接入策略研发流，结束其"独立模块未入主图"状态；主智能体不感知算子概念，避免抽象泄漏
- **ADR-010**（HTR 假设树）：**Enhanced by ADR-016**。六步循环与树哲学保留，executor 节点引入有限逃生口。`HypothesisTree` 领域实体保留
- **ADR-011**（jinja2 prompt 模板）：新增工具 prompt 统一用 `MarkdownPromptTemplate`，遵循 `{{ var }}` 语法
- **ADR-012**（大师智能节点）：`ideate` 节点内部仍调用 PersonaRegistry，能力保留
- **ADR-013**（回测准确性原则）：硬约束保留，节点内部强制 PIT 对齐与三段式分割
- **ADR-014**（本体论连接器）：`observe` 节点内部可调 Connector 图谱查询，能力保留
- **ADR-015**（统计过拟合门）：三道统计门在合并门内部强制执行，逃生口不触及统计门
- **ADR-017**（自我进化能力）：从本 ADR 拆出，独立评审，状态 Deferred

## 参考资料

- [LangGraph ReAct Agent 文档](https://langchain-ai.github.io/langgraph/reference/agents/#langgraph.prebuilt.chat_agent_executor.create_react_agent)
- [Arbor 论文（ADR-010 参考）](https://arxiv.org/abs/2606.11926) — 假设树精炼哲学
- 实测发现 prompt 问题清单：见本 ADR I 节
