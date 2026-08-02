# 架构决策记录 (ADR) 索引

ADR 提供决策背景与方案选型理由。具体的实施进度、Phase 完成状态、扩展方法数等动态信息以代码为准，请直接阅读对应 ADR 文档与源码。

## 当前有效

| 编号 | 标题 | 简述 |
|------|------|------|
| [ADR-001](001-yaml-dsl-strategy.md) | YAML DSL 策略描述 | 替代 Python/qlib |
| [ADR-002](002-partial-node-injection.md) | `functools.partial` 节点注入 | 替代闭包 |
| [ADR-005](005-event-driven-backtest.md) | 事件驱动回测框架 | 替代向量化引擎。优先保证可信性（杜绝未来函数）与复杂策略表达力，速度为次要目标 |
| [ADR-006](006-ciccwm-data-provider.md) | ciccwm 财经数据 Provider | 纯 HTTP、零本地依赖第四数据源，补齐财务报表/资金流向/排行/关联板块/热榜资讯 |
| [ADR-007](007-unified-substance-architecture.md) | 物质-运动统一架构 | `Substance`（Pydantic）统一事件/关系/知识/策略经验；双索引（keyword+semantic + GraphIndex 邻接表）；JSONL 持久化无 pickle |
| [ADR-008](008-parallel-backtest-and-unified-templating.md) | 并行回测 + 统一模板渲染 | 进程级并行编排层（SharedMemory 零拷贝 + ProcessPoolExecutor）+ 参数网格。**A 部分（`${var}` 语法 + 纯函数渲染器）已被 ADR-011 废弃**，B 部分（并行回测编排）继续有效，2026-08 增补 B5（warmup 注入契约）+ B6（diagnostics 保真约束） |
| [ADR-009](009-operator-catalog-and-operator-dev-subgraph.md) | 算子目录 + 算子研发子图 | 类型化算子目录（`@operator` + Pydantic params + 约定目录自动扫描）；`prove_causality` 因果性证明作算子上线硬约束；operator_dev 异步闭环 + strategy_optimization 验收 |
| [ADR-010](010-hypothesis-tree-refinement.md) | 假设树精炼 HTR | `strategy_rd` 子图 Arbor HTR 六步循环 + 持久化假设树 + Walk-Forward held-out 合并门。Enhanced by ADR-015（三道统计门）+ ADR-016（executor 有限逃生口）。**阶段 5 并行机制于 2026-08 收尾修正**：Send fan-out 伪并行 -> executor 内批量并行（受 ADR-008 B5/B6 约束） |
| [ADR-011](011-unified-mustache-prompt-templating.md) | 统一 jinja2 + ChatPromptTemplate | `${var}` -> `{{ var }}`（默认不 HTML 转义，与 JSON `{}` 不冲突）；多消息结构用 `MarkdownChatPromptTemplate` |
| [ADR-012](012-persona-subgraph-skill-pack.md) | 大师智能节点技能包 | `MasterPersona` Protocol + `PersonaRegistry`，支持 stock_analysis / strategy_review / strategy_generate / result_synthesis 多 mode |
| [ADR-013](013-backtest-accuracy-principles.md) | 回测引擎准确性原则与陷阱清单 | 七维分类框架（数据正确性/时序偏差/交易执行/市场微观结构/投资组合与风控/指标计算/工程与审计）+ 检测方法论 + 防护状态总览。2026-08 增补 T6（warmup 漏算致因子前视截断） |
| [ADR-014](014-ontology-connector.md) | 本体论连接器 + DataConnector 全能力接入 | `Connector.get_concept` 作为上层唯一数据访问入口，aspect 字符串经 `ConceptResolver` 解析为 `ResolutionKind` |
| [ADR-015](015-statistical-overfitting-gates.md) | 统计过拟合门与反馈闭环修复 | 三道统计门（Walk-Forward 稳定性 / DSR / PBO）+ 失败信号上行 + select 多样性修复；防 Q1/Q2 窗口不一致与 selection bias |
| [ADR-016](016-hierarchical-agent-architecture.md) | 分层智能体架构 | 主智能体 ReAct 任务分解 + 子图工具化。**§C「策略研发不做全量 ReAct」已被 ADR-018 Supersede**；Master 仍负责任务分解，策略飞轮改由 ResearchAgent |
| [ADR-017](017-self-evolution-capability.md) | 自我进化能力 | 经验回写/热启动/元指标/失败反思/prompt自审。全自主 + 版本追溯。状态 Deferred，前置条件：ADR-015 统计门端到端验证 + ADR-016 主智能体落地 + 稳健策略基线产出 |
| [ADR-018](018-think-on-graph-research-agent.md) | ToG 策略研发飞轮 | HTR 控制器 → ResearchAgent（LLM ⊗ Graph）；假设树/统计门保留为状态与硬约束；事件 `prepare_context` 基础设施化；数据层取消降级叙事 |

## 已废弃 / 已退役

| 编号 | 标题 | 状态 |
|------|------|------|
| [ADR-003](003-ast-safe-evaluator.md) | AST 白名单表达式求值替代 `eval()` | Superseded by ADR-009，已于 2026-07 收尾删除 |
| [ADR-004](004-memory-system.md) | numpy/pandas 三级记忆系统替代 Qdrant | Superseded by ADR-007，旧 `memory/` 模块已删除 |

---

## ADR 编写指南

> 本指南基于 Michael Nygard 2011 年开创性文章《Documenting Architecture Decisions》与 [adr.github.io](https://adr.github.io/) 推广的开源社区最佳实践。

### 何时写 ADR

只记录"架构显著"的决策——影响结构、非功能特性、依赖关系、接口或构建技术的决策。小决策不写。一个 ADR 只描述一个决策。

### 文件命名

`NNNN-短横线分隔的英文标题.md`（如 `007-unified-substance-architecture.md`），四位数字前缀，编号**顺序单调递增**。

### 文件结构（五段式）

| 段落 | 内容 | 写作要点 |
|------|------|----------|
| **标题** | 短名词短语 | 如"ADR 1: 部署于 Ruby on Rails 3.0.10" |
| **状态** | Proposed / Accepted / Deprecated / Superseded | Superseded 必须链接到新 ADR |
| **背景（Context）** | 驱动决策的力（技术/政治/社会/项目本地） | **价值中立**，只陈述事实，指出张力 |
| **决策（Decision）** | 对这些力的回应 | 主动语态完整句子，"我们将……" |
| **后果（Consequences）** | 应用决策后的新上下文 | **正面 + 负面 + 中性**全部列出，不只写好的 |

### 核心原则

1. **不可变编号**：一旦分配，编号永不复用；废弃 ADR 保留文件，仅改状态字段。
2. **决策反转就新建**：要推翻旧决策，写新 ADR 并将旧 ADR 标记为 `Superseded by ADR-XXX`，旧文件不删除。
3. **短小精悍**：1-2 页为限，bite-sized 才会被阅读和持续维护，长文档没人读也没人更新。
4. **对话式写作**：像与未来开发者对话，完整句子成段，列表只用于视觉组织，不写残句。
5. **背景中立**：背景部分只描述事实和张力，不带倾向；决策部分才表态。
6. **后果必含负面**：只写好后果的 ADR 等于宣传稿，负面后果才是后续决策的真实背景。
7. **后果即下一代背景**：本 ADR 的后果会成为后续 ADR 的背景，形成"决策谱系"。

### 状态机

```
Proposed → Accepted → Deprecated
              ↓
         Superseded（链接到新 ADR）
```

### 存放与版本控制

ADR 与代码同仓库版本控制（本仓库 `docs/adr/`），便于追踪历史。GitHub 自动渲染 Markdown，无需额外 Wiki。

### 参考来源

- [Documenting Architecture Decisions — Michael Nygard, 2011](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions.html)（ADR 概念奠基文章）
- [adr.github.io](https://adr.github.io/)（ADR 社区主页，含模板与工具链）
- [Azure Well-Architected Framework: ADR](https://learn.microsoft.com/en-us/azure/well-architected/architect-role/architecture-decision-record)（微软云架构框架推荐实践）
- [AWS Prescriptive Guidance: ADR](https://docs.aws.amazon.com/prescriptive-guidance/latest/architectural-decision-records/welcome.html)（AWS 处方式指南）
