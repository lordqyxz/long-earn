# 架构决策记录 (ADR) 索引

ADR 只记录决策背景与方案选型理由；实施进度、Phase 状态、文件清单、覆盖率等动态信息以代码为准，请直接阅读对应源码。

**运行时总览（ADR-018 后）**：[architecture.md](../architecture.md)

> **2026-08 压缩说明**：全部 ADR 已压缩为「背景 / 决策 / 后果」精华版，删除已完成的分阶段实施计划、实施状态表、与源码重复的代码示例，以及被取代后不再有效的历史原文（git 历史可查全文）；已退役且仅剩历史价值的 ADR-003 / ADR-004 删除文件，仅保留下方索引行。

## 当前有效

| 编号 | 标题 | 简述 |
|------|------|------|
| [ADR-001](001-yaml-dsl-strategy.md) | YAML DSL 策略描述 | 替代 LLM 生成 Python/qlib |
| [ADR-002](002-partial-node-injection.md) | `functools.partial` 节点注入 | 替代闭包，节点可独立测试 |
| [ADR-005](005-event-driven-backtest.md) | 事件驱动回测框架 | 替代向量化引擎。可信性（杜绝未来函数）> 表达力 > 速度 |
| [ADR-006](006-ciccwm-data-provider.md) | ciccwm 财经数据 Provider | 纯 HTTP 零本地依赖；情报独占能力（资金流向/排行/板块/热榜），面板次选源 |
| [ADR-007](007-unified-substance-architecture.md) | 物质-运动统一架构 | `Substance`（Pydantic）统一事件/关系/知识/策略经验；双索引（关键词+语义 / 图）；PG 持久化。附录：PIT 数据修复（announce_date 必填、财务统一 miniqmt、18 字段） |
| [ADR-008](008-parallel-backtest-and-unified-templating.md) | 并行回测编排 + 参数网格 | SharedMemory 零拷贝 + ProcessPoolExecutor；B5 warmup 注入契约、B6 diagnostics 保真与串行/批量等价性硬约束。A 部分模板渲染已被 ADR-011 废弃 |
| [ADR-009](009-operator-catalog-and-operator-dev-subgraph.md) | 算子目录 + 算子研发子图 | 类型化算子目录（Pydantic params 解析期校验）；`prove_causality` 因果性数学证明作上线硬约束；operator_dev 异步闭环；sharpe 验收门 |
| [ADR-010](010-hypothesis-tree-refinement.md) | 假设树精炼 HTR | 假设树 + held-out 合并门。**编排控制器已由 ADR-018 移交 ResearchAgent**，HTR 降为状态/脚手架 |
| [ADR-011](011-unified-mustache-prompt-templating.md) | 统一 jinja2 + ChatPromptTemplate | `{{ var }}` 默认不 HTML 转义；多消息结构 `MarkdownChatPromptTemplate`。附录：RealtimeDataProvider + 价格告警 + 资金流向分析师（第 5 视角） |
| [ADR-012](012-persona-subgraph-skill-pack.md) | 大师智能节点技能包 | `MasterPersona` Protocol + `PersonaRegistry`，四 mode（stock_analysis / strategy_review / strategy_generate / result_synthesis），新增大师三步零拓扑改动 |
| [ADR-013](013-backtest-accuracy-principles.md) | 回测准确性原则与陷阱清单 | 七维 41 陷阱（数据/时序/执行/微观结构/风控/指标/审计）+ 检测方法论（因果性测试为最强单一手段） |
| [ADR-014](014-ontology-connector.md) | 本体论 + 连接器 | `Connector.get_concept` 单一概念查询入口（上层不碰字段名/数据源/PIT）；OntologyGraph 跨域遍历；图驱动记忆激活；财务 8 细表 |
| [ADR-015](015-statistical-overfitting-gates.md) | 统计过拟合门 | 三道门（Walk-Forward 稳定性 / DSR / PBO）串行追加在合并门内；失败信号上行；select 多样性修复 |
| [ADR-016](016-hierarchical-agent-architecture.md) | 分层智能体架构 | MasterAgent（ReAct）任务分解 + 子图工具化；防过拟合硬约束表（证据约束不可跳过）。§C 编排条款已被 ADR-018 Supersede |
| [ADR-017](017-self-evolution-capability.md) | 自我进化能力 | 经验回写/热启动/元指标/失败反思/prompt 自审。**Deferred**，前置：统计门验证有效 + 稳健策略基线产出 |
| [ADR-018](018-think-on-graph-research-agent.md) | ToG 策略研发飞轮 | HTR 控制器 -> ResearchAgent（LLM ⊗ Graph）；假设树/统计门保留为状态与硬约束；`prepare_context` 事件基础设施化；数据层取消降级叙事 |
| [ADR-019](019-postgresql-unified-storage.md) | 统一存储迁移至 PostgreSQL | DuckDB 三库 -> PostgreSQL（Docker `pg`）；MVCC 多写者并发安全（并行回测审计不丢行） |
| [ADR-020](020-wide-panel-materialization.md) | PG 宽表物化 + ADBC 直读 | `panel_daily` 手工增量物化视图（脏标记 + 惰性 symbol 级重建）替代 panel 文件缓存；ADBC Arrow 零拷贝直读；降级回退旧路径 |

## 已废弃 / 已退役

| 编号 | 标题 | 状态 |
|------|------|------|
| ADR-003 | AST 白名单表达式求值替代 `eval()` | Superseded by ADR-009，2026-07 退役，文件已删（git 历史可查） |
| ADR-004 | numpy/pandas 三级记忆系统替代 Qdrant | Superseded by ADR-007，旧 `memory/` 已删，文件已删（git 历史可查） |

---

## ADR 编写与维护指南

> 基于 Michael Nygard 2011《Documenting Architecture Decisions》与 [adr.github.io](https://adr.github.io/) 最佳实践。

### 何时写 ADR

只记录「架构显著」的决策--影响结构、非功能特性、依赖关系、接口或构建技术。小决策不写；一个 ADR 只描述一个决策。

### 文件与结构

- 命名 `NNNN-短横线英文标题.md`，四位数字前缀，编号顺序单调递增、**永不复用**。
- 五段式：**标题**（短名词短语）/ **状态**（Proposed / Accepted / Deprecated / Superseded，Superseded 必须链接新 ADR）/ **背景**（价值中立，只陈述事实与张力）/ **决策**（主动语态「我们将……」）/ **后果**（正面 + 负面 + 中性全列，只写好的等于宣传稿）。

### 维护原则

1. **决策反转就新建**：推翻旧决策写新 ADR，旧 ADR 标 `Superseded by ADR-XXX`。
2. **短小精悍**：正文限于「背景/决策/后果」，不维护实施进度表、Phase 状态、文件清单、行号引用--这些以代码为准，写在 ADR 里必然过时并污染上下文。
3. **及时压缩**：实施完成后，删除分阶段实施计划与被取代的历史原文（git 历史即归档）；仅剩历史价值（实现已删、无现行参考点）的退役 ADR 可删文件，索引保留编号行与去向。
4. **后果即下一代背景**：本 ADR 的后果是后续 ADR 的背景，形成决策谱系。
