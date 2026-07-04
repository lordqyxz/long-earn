# ADR-012: 交易大师 Persona Subgraph 技能包

日期: 2026-07
状态: Accepted (仅架构决策，实施待准确性归因后启动)

## 背景

当前 4 个交易大师分析师（Buffett / CharlesMunger / Fiske / Petter）实现为 `stock_analysis` 子图内的 4 个独立节点，每个节点是**单次 LLM 调用**：

```
stock_analysis subgraph
├── get_stock_data          # 准备 stock_data dict
├── buffett_analysis        # MarkdownChatPromptTemplate.format → llm.invoke(messages) → str
├── charles_munger_analysis # 同上
├── fiske_analysis          # 同上
├── petter_analysis         # 同上
└── summarize               # 字符串拼接（不调 LLM）
```

### 现状局限

1. **仅服务 stock_analysis**：4 个大师视角只在股票分析子图用。`strategy_rd` 的反思循环（`reflect` / `observe`）和主入口（`agent.py::_summarize_node`）无法调用大师视角，错失「以巴菲特视角审视策略」或「大师共识汇总」的深度。

2. **单次 LLM 调用，无推理深度**：大师分析是「一次性判断」——`llm.invoke(prompt) → str`。无中间推理步骤、无工具调用、无假设验证。对于复杂标的（如护城河评估需要对比历史 ROE 趋势、行业对比、管理层访谈），单次 prompt 信息量不足。

3. **prompt 资产散落**：4 个大师的 `.md` prompt 在 `stock_analysis/agents/` 目录，few-shot 在各自 `.py` 的 `EXAMPLES` 常量，无集中管理。`strategy_rd` 若想复用巴菲特视角，需手工拷贝 prompt + few-shot。

4. **无并行能力**：4 个大师串行调用（`buffett → munger → fiske → petter`），即使它们之间无数据依赖。

### 升级动机：Mini Agent

将每个交易大师从「单次 LLM 调用」升级为「mini agent」——有自己的状态、多步推理、可选工具调用。预期收益：

- **数据获取层**：mini agent 可主动调用工具补数据（查行业平均 PE、查历史估值区间、查同业对比），分析维度更全
- **推理深度**：支持「假设→验证→修正」迭代，比单次 prompt 更接近真实分析师工作流
- **可解释性**：中间步骤（工具调用、推理链）可追溯，比黑盒输出更可审计

**风险**：成本 ×3-5（每个 mini agent 多步 LLM 调用）、引入新错误源（agent 决策错误）、边际收益取决于当前准确性瓶颈在哪（若瓶颈是数据质量而非推理深度，mini agent 不解决问题）。

## 决策

将 4 个交易大师升级为 **Persona Subgraph**——每个大师是一个独立的 LangGraph 子图（mini agent），作为可复用技能包注入到任何消费方。

### A. 架构：Persona Subgraph

每个大师 = 一个编译后的 `StateGraph`，包含多步推理节点：

```
src/long_earn/skills/personas/
├── __init__.py                    # 导出 4 个 compiled subgraph + 工厂函数
├── base.py                        # PersonaState 基类 + create_persona_subgraph 工厂
├── buffett.py                     # 巴菲特 mini agent（StateGraph 定义）
├── charles_munger.py
├── fiske.py
├── petter.py
├── tools/                         # 大师可调用的工具
│   ├── __init__.py
│   ├── valuation.py               # 估值查询（PE/PB 历史分位、行业对比）
│   ├── financials.py              # 财务指标查询（ROE 趋势、现金流、负债）
│   └── market_context.py          # 市场环境查询（行业景气度、宏观）
└── prompts/                       # .md prompt 集中管理
    ├── buffett/
    │   ├── system.md              # 系统消息（角色 + 框架）
    │   ├── observe.md             # 观察节点 prompt
    │   ├── analyze.md             # 分析节点 prompt
    │   └── conclude.md            # 结论节点 prompt
    ├── charles_munger/
    ├── fiske/
    └── petter/
```

### B. 单个 Persona Subgraph 内部拓扑

以巴菲特为例，mini agent 内部 3 节点循环：

```
START → observe → analyze ↔ validate → conclude → END
```

| 节点 | 职责 | LLM 调用 | 工具调用 |
|------|------|----------|----------|
| `observe` | 接收 stock_data，识别关键分析维度（护城河/管理层/财务/估值） | 1 次 | 可选（查行业景气度补充上下文） |
| `analyze` | 针对每个维度生成假设 + 论据 | 1 次 | 可选（查历史 ROE、查同业 PE） |
| `validate` | 验证假设是否成立，若不成立返回 analyze 重新推理 | 1 次 | 无 |
| `conclude` | 汇总验证后的假设，输出结构化结论 | 1 次 | 无 |

**循环控制**：`analyze ↔ validate` 最多 2 轮（防死循环），第 2 轮后强制进入 `conclude`。

**状态 schema**：

```python
class BuffettState(TypedDict):
    stock_data: dict[str, Any]          # 输入：股票数据
    dimensions: list[str]               # observe 产出：分析维度
    hypotheses: list[dict]              # analyze 产出：[{dimension, hypothesis, evidence, confidence}]
    validation_results: list[dict]      # validate 产出：[{hypothesis, valid, reason}]
    conclusion: dict[str, Any]          # conclude 产出：{verdict, rationale, key_metrics, risks}
    iteration_count: int                # 循环计数
```

### C. 三层消费方复用

#### 消费方 1：stock_analysis 子图（现有用法，升级）

```python
# stock_analysis/subgraph.py
from long_earn.skills.personas import buffett_subgraph, munger_subgraph, ...

def create_stock_analysis_subgraph():
    graph = StateGraph(State)
    graph.add_node("get_stock_data", get_stock_data_node)
    # 大师子图作为节点嵌入
    graph.add_node("buffett", buffett_subgraph)
    graph.add_node("munger", munger_subgraph)
    graph.add_node("fiske", fiske_subgraph)
    graph.add_node("petter", petter_subgraph)
    graph.add_node("summarize", summarize_node)
    # 4 个大师并行（LangGraph Send API）
    graph.add_conditional_edges("get_stock_data", route_to_all_masters)
    graph.add_edge(["buffett", "munger", "fiske", "petter"], "summarize")
    return graph.compile()
```

**并行优化**：4 个大师子图通过 `Send` API 并行执行，延迟从串行 4×降为 1×（假设单大师内部延迟相当）。

#### 消费方 2：strategy_rd 反思（新增）

```python
# strategy_rd/agents/strategy_research_agent.py::reflect
def reflect(strategy, backtest_result):
    # 调用巴菲特 mini agent 审视策略
    buffett_view = buffett_subgraph.invoke({
        "stock_data": strategy,  # 复用 stock_data 槽位传策略
        "mode": "strategy_review",  # 新模式：策略审视而非股票分析
    })
    # buffett_view["conclusion"] 含巴菲特视角的弱点 + 改进建议
    return {"buffett_perspective": buffett_view["conclusion"]}
```

**模式扩展**：Persona Subgraph 支持多模式：
- `mode="stock_analysis"`（默认）：分析股票
- `mode="strategy_review"`：审视策略（输入是策略 + 回测结果）
- `mode="result_synthesis"`：综合结果（输入是多路结果）

通过 `mode` 参数切换内部 prompt 选择，子图拓扑不变。

#### 消费方 3：主入口汇总（新增）

```python
# agent.py::_summarize_node
def summarize_node(state):
    # 用「大师共识」模式汇总三路结果
    views = [
        buffett_subgraph.invoke({"stock_data": state, "mode": "result_synthesis"}),
        munger_subgraph.invoke({"stock_data": state, "mode": "result_synthesis"}),
    ]
    return {"summary": consensus(views)}
```

### D. Persona Subgraph 作为 LangGraph 节点

LangGraph 的 `add_node` 原生接受 compiled graph 作为节点：

```python
# 父图直接嵌入子图
parent_graph.add_node("buffett", buffett_subgraph)
```

子图的 `invoke` 输入是父图 state 的子集（通过 `state_schema` 映射），输出合并回父图 state。

**状态隔离**：每个大师子图有独立 `PersonaState`，不污染父图 state。父图只读取 `conclusion` 字段。

**Checkpointing**：每个大师子图可独立 checkpoint，支持 human-in-loop（如巴菲特分析到一半，人工介入修正假设）。

### E. 工具集成

大师 mini agent 可调用工具补数据：

```python
# skills/personas/tools/valuation.py
@tool
def get_pe_percentile(stock_code: str, years: int = 5) -> dict:
    """查询 PE 历史分位"""
    ...

# buffett.py 的 analyze 节点
analyze_prompt = ChatPromptTemplate.from_messages([
    ("system", "...使用工具补充数据..."),
    ("human", "{{ stock_data }}\n已有假设：{{ hypotheses }}"),
])
analyze_chain = analyze_prompt | llm.bind_tools([get_pe_percentile, ...])
```

工具调用通过 `ToolNode` 或 `llm.bind_tools` + 条件边实现 ReAct 循环。

**工具复用**：4 个大师共享工具集（`skills/personas/tools/`），避免重复实现。

### F. Prompt 集中管理

4 个大师的 prompt 从 `stock_analysis/agents/` 迁移到 `skills/personas/prompts/`，每个大师一个子目录：

```
skills/personas/prompts/buffett/
├── system.md          # 角色定义（含 mode 分支）
├── observe.md         # 观察节点
├── analyze.md         # 分析节点
└── conclude.md        # 结论节点
```

`system.md` 的 frontmatter 声明 mode 分支：

```yaml
---
version: 2.0.0
description: 巴菲特价值投资分析
modes:
  stock_analysis:
    system: |
      你是沃伦·巴菲特，分析这只股票的投资价值...
  strategy_review:
    system: |
      你是沃伦·巴菲特，审视这个量化策略是否符合价值投资原则...
  result_synthesis:
    system: |
      你是沃伦·巴菲特，从价值投资视角综合以下研究结果...
---
```

## 理由

1. **LangGraph 原生子图嵌套**：`add_node` 直接接受 compiled graph，无需额外抽象层。子图是一等公民，支持 checkpointing / streaming / human-in-loop。
2. **mini agent 提升推理深度**：多步推理（observe→analyze→validate→conclude）比单次 prompt 更接近真实分析师工作流，支持假设验证修正循环。
3. **三消费方统一复用**：stock_analysis / strategy_rd / agent.py 都用 `persona_subgraph.invoke(input)` 调用，通过 `mode` 参数切换语义。
4. **并行能力**：4 个大师子图通过 `Send` API 并行，延迟从 4× 降为 1×。
5. **工具集成扩展数据维度**：mini agent 可主动查数据（PE 分位、ROE 趋势），突破「上游准备什么就分析什么」的限制。
6. **Prompt 资产集中**：`skills/personas/prompts/` 集中管理，消除散落和重复。

## 后果

### 正面

- **分析深度提升**：从单次 LLM 判断升级为多步推理 + 工具调用
- **跨消费方复用**：strategy_rd 反思可调用大师视角，主入口汇总可用大师共识
- **并行化**：4 大师并行，延迟降低
- **可审计性**：mini agent 中间步骤可追溯

### 负面

- **成本上升 ×3-5**：每个大师从 1 次 LLM 调用变为 3-5 次（observe + analyze + validate + conclude + 工具调用）。4 大师并行后总 LLM 调用从 4 次变为 12-20 次。
- **延迟可能不降反升**：虽然 4 大师并行，但单个大师内部多步推理的延迟 > 单次调用。若单大师内部 4 步串行 ×2s = 8s，并行后总延迟 8s vs 原串行 4×2s = 8s，持平。但工具调用会增加延迟。
- **复杂度大增**：从 4 个简单节点变为 4 个子图 + 工具集 + 多模式 prompt。维护成本显著上升。
- **新错误源**：agent 决策错误（选错工具、误读中间结果、validate 误判）可能比单次 prompt 更糟。ReAct 循环失败时降级策略需设计。
- **状态映射复杂**：父图 state 与子图 `PersonaState` 的映射需维护。

### 风险缓解

1. **准确性归因前置**：实施前先做一次归因分析（抽样 20 案例，标注错误类型）。若瓶颈是「数据缺失」而非「推理错误」，应先补工具/数据源而非升级 mini agent。
2. **渐进式实施**：先做 1 个大师（巴菲特）的 mini agent，对比单次 prompt 的准确性和成本，验证收益后再扩展到 4 个。
3. **降级策略**：mini agent 失败时（工具不可用 / 循环超时）降级为单次 prompt 调用，保证可用性。
4. **成本上限**：每个大师子图设 `max_iterations` 硬上限（如 analyze↔validate 最多 2 轮），防止失控。

## 实施前置条件

> **本 ADR 仅记录架构决策，实施待以下前置条件完成：**

1. **ADR-011 阶段 4 提交**：4 分析师已迁移到 `MarkdownChatPromptTemplate`，但 strategy_rd HTR 节点未迁移。Persona Subgraph 实施前应先完成 ADR-011 全部阶段，确保 prompt 基础设施稳定。
2. **准确性归因分析**：抽样 20 个现有分析案例，标注错误类型（数据缺失 / 推理错误 / prompt 误导 / LLM 能力不足）。若「推理错误」占比 < 30%，暂不实施 mini agent，先优化其他瓶颈。
3. **工具层就绪**：`skills/personas/tools/` 的估值/财务/市场工具需先实现并测试。工具不可用则 mini agent 退化为多步 prompt（仍有收益但大打折扣）。

## 分阶段实施计划（待启动）

### 阶段 1：基础设施 + 巴菲特原型

- 新建 `src/long_earn/skills/personas/` 目录结构
- 实现 `base.py`：`PersonaState` + `create_persona_subgraph` 工厂
- 实现巴菲特 mini agent（observe/analyze/validate/conclude 4 节点）
- 实现 `tools/valuation.py` + `tools/financials.py` 基础工具
- 巴菲特 prompt 迁移到 `prompts/buffett/`（多 mode）
- stock_analysis 子图：巴菲特节点升级为子图嵌套，其余 3 大师暂保持单次调用
- 对比测试：巴菲特 mini agent vs 原单次 prompt，在 20 个案例上对比准确性和成本
- **决策门**：若准确性提升 < 15% 或成本上升 > 5×，停止扩展，回退巴菲特为单次 prompt

### 阶段 2：扩展到 4 大师 + 并行

- 基于阶段 1 验证结果，扩展 CharlesMunger / Fiske / Petter 三个 mini agent
- stock_analysis 子图：4 大师全部升级为子图，用 `Send` API 并行
- 每个大师的工具集差异化（如 Fiske 侧重成长性指标，Buffett 侧重护城河）

### 阶段 3：strategy_rd 反思接入

- `StrategyResearchAgent.reflect` 调用大师子图（`mode="strategy_review"`）
- HTR `observe` 节点可调用大师视角补充观察
- 测试：反思质量是否提升

### 阶段 4：主入口汇总接入

- `agent.py::_summarize_node` 调用大师子图（`mode="result_synthesis"`）
- 可选：大师共识模式（4 大师并行汇总，取共识）
- 测试：汇总质量是否提升

## 与其他 ADR 的关系

- **ADR-011**（jinja2 + ChatPromptTemplate）：Persona Subgraph 的 prompt 继续用 `MarkdownChatPromptTemplate` + jinja2 `{{ var }}`。本 ADR 是 prompt 资产的消费方升级，不改变渲染层。
- **ADR-010**（HTR 假设树）：strategy_rd 反思阶段若调用大师子图，是 HTR `observe` / `reflect` 节点的增强，不改变 HTR 拓扑。
- **ADR-009**（算子目录）：`operator_dev` 的 Spec + Backlog + Protocol 模式是本 ADR 的设计参考，但 Persona Subgraph 不需要 Backlog（大师是同步调用的，不是异步任务队列）。
- **ADR-002**（partial 节点注入）：Persona Subgraph 作为节点嵌入父图时，仍遵循 ADR-002 的依赖注入模式（通过 `RuntimeContext` 获取 `llm` / `stock_service` 等）。

## 参考文献

- LangGraph Subgraphs: https://langchain-ai.github.io/langgraph/concepts/subgraphs/
- LangGraph Send API (并行分发): https://langchain-ai.github.io/langgraph/concepts/multi_agent/
- LangChain ToolNode: https://langchain-ai.github.io/langgraph/concepts/agentic_concepts/
- ReAct Agent 模式: https://arxiv.org/abs/2210.03629
- ADR-011 jinja2 + ChatPromptTemplate: `docs/adr/011-unified-mustache-prompt-templating.md`
- ADR-010 HTR 假设树: `docs/adr/010-hypothesis-tree-refinement.md`
