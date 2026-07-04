# ADR-012: 大师智能节点（Master Persona Node）可复用技能包

日期: 2026-07
状态: Accepted

## 背景

当前 4 个交易大师（Buffett / CharlesMunger / Fiske / Petter）实现为 `stock_analysis` 子图内的 4 个独立节点，每个节点是**单次 LLM 调用**，且**只服务 stock_analysis 一个消费方**。

### 现状局限

1. **仅服务 stock_analysis**：`strategy_rd` 的策略生成（`research` 节点）和策略反思（`reflection` 节点）无法调用大师视角。例如「巴菲特审视这个策略是否符合价值投资原则」「芒格从多元思维模型角度找策略弱点」这些高价值能力无法复用。

2. **大师能力不可扩展**：4 个大师硬编码在 `stock_analysis/agents/` 目录，新增一位大师（如索罗斯、利弗莫尔）要修改 subgraph 拓扑 + 新建 agent 文件 + 新建 prompt，无插件化机制。

3. **prompt 资产散落**：4 个大师的 `.md` prompt 在 `stock_analysis/agents/`，few-shot 在各自 `.py` 的 `EXAMPLES` 常量。若 `strategy_rd` 想复用巴菲特视角，需手工拷贝 prompt + few-shot，维护漂移风险高。

4. **无统一调用契约**：stock_analysis 的调用方是 `agent.analyze(stock_data, event_context) -> str`，若 strategy_rd 想调用，接口不匹配（策略审视的输入是 strategy + backtest_result，不是 stock_data）。

### 需求

用户要求：
1. 大师分析做成**可复用的智能节点**
2. 允许**策略生成**和**策略反思**阶段利用大师能力
3. 允许用户**扩展更多的大师经验**

## 决策

将交易大师升级为 **Master Persona Node**——每个大师是一个实现统一 Protocol 的智能节点，通过注册表管理，可被任何消费方（stock_analysis / strategy_rd research / strategy_rd reflection / agent.py summarize）按 name 注入调用。

### A. 核心抽象：MasterPersona Protocol

定义统一调用契约，所有大师实现此 Protocol：

```python
# src/long_earn/skills/personas/protocol.py
from typing import Protocol, Any

class MasterPersona(Protocol):
    """交易大师智能节点协议"""

    name: str               # 大师标识（如 "buffett"）
    display_name: str        # 展示名（如 "沃伦·巴菲特"）
    perspective: str         # 视角摘要（如 "价值投资 / 护城河"）

    def analyze(self, context: PersonaContext) -> PersonaResult:
        """分析入口

        Args:
            context: 统一上下文，含 mode + 目标数据 + 可选工具

        Returns:
            结构化结果（verdict / rationale / weaknesses / suggestions）
        """
        ...
```

### B. 统一上下文与结果

```python
# src/long_earn/skills/personas/types.py
from typing import Literal, Any
from pydantic import BaseModel

PersonaMode = Literal[
    "stock_analysis",      # 分析股票（stock_analysis 子图用）
    "strategy_review",     # 审视策略（strategy_rd reflection 用）
    "strategy_generate",   # 参与策略生成（strategy_rd research 用）
    "result_synthesis",    # 综合结果（agent.py summarize 用）
]

class PersonaContext(BaseModel):
    mode: PersonaMode
    target: dict[str, Any]         # 分析对象（stock_data / strategy / results）
    backtest_result: dict | None = None    # 回测结果（strategy_review 模式用）
    event_context: str = ""                # 市场事件上下文
    available_tools: list[str] = []        # 可用工具名列表

class PersonaResult(BaseModel):
    verdict: str                   # 结论（买入/持有/卖出 或 接受/改进/拒绝）
    rationale: str                 # 详细理由
    weaknesses: list[str] = []     # 识别的弱点（strategy_review 模式）
    suggestions: list[str] = []    # 改进建议（strategy_review / strategy_generate 模式）
    confidence: float = 0.0        # 置信度 0-1
    raw_analysis: str = ""         # 原始分析文本（可审计）
```

### C. 大师实现

每个大师是一个实现 `MasterPersona` 的类，内部用 `MarkdownChatPromptTemplate` 加载多 mode prompt：

```python
# src/long_earn/skills/personas/buffett.py
class BuffettPersona:
    name = "buffett"
    display_name = "沃伦·巴菲特"
    perspective = "价值投资 / 护城河 / 长期持有"

    def __init__(self, llm_service: LLMService):
        self._llm = llm_service
        # 多 mode prompt：每个 mode 对应一个 .md 文件
        self._prompts = {
            "stock_analysis": MarkdownChatPromptTemplate(
                "prompts/buffett/stock_analysis.md", caller_file=__file__
            ),
            "strategy_review": MarkdownChatPromptTemplate(
                "prompts/buffett/strategy_review.md", caller_file=__file__
            ),
            "strategy_generate": MarkdownChatPromptTemplate(
                "prompts/buffett/strategy_generate.md", caller_file=__file__
            ),
            "result_synthesis": MarkdownChatPromptTemplate(
                "prompts/buffett/result_synthesis.md", caller_file=__file__
            ),
        }
        # few-shot 按 mode 区分
        self._examples = {
            "stock_analysis": [...],  # 从原 buffett_analyst.py EXAMPLES 迁移
            "strategy_review": [...], # 新增：策略审视示例
        }

    def analyze(self, context: PersonaContext) -> PersonaResult:
        prompt = self._prompts[context.mode]
        examples = self._examples.get(context.mode, [])
        messages = prompt.format_messages(
            target=context.target,
            backtest_result=context.backtest_result or {},
            event_context=context.event_context,
            examples=examples,
        )
        response = self._llm.invoke(messages)
        return self._parse_result(response, context.mode)
```

### D. 注册表与扩展机制

```python
# src/long_earn/skills/personas/registry.py
class PersonaRegistry:
    """大师注册表"""

    _personas: dict[str, type[MasterPersona]] = {}

    @classmethod
    def register(cls, persona_class: type[MasterPersona]) -> type[MasterPersona]:
        """注册大师类（可作为装饰器使用）

        用法：
            @PersonaRegistry.register
            class BuffettPersona:
                ...
        """
        cls._personas[persona_class.name] = persona_class
        return persona_class

    @classmethod
    def get(cls, name: str) -> type[MasterPersona]:
        return cls._personas[name]

    @classmethod
    def all(cls) -> dict[str, type[MasterPersona]]:
        return dict(cls._personas)

    @classmethod
    def create_all(cls, llm_service: LLMService) -> dict[str, MasterPersona]:
        """创建所有已注册大师的实例"""
        return {name: cls(llm_service) for name, cls in cls._personas.items()}
```

### E. 扩展新大师

用户新增大师只需 2 步：

**步骤 1**：创建大师实现类

```python
# src/long_earn/skills/personas/livermore.py
from long_earn.skills.personas.registry import PersonaRegistry
from long_earn.skills.personas.protocol import MasterPersona, PersonaContext, PersonaResult

@PersonaRegistry.register
class LivermorePersona:
    name = "livermore"
    display_name = "杰西·利弗莫尔"
    perspective = "趋势交易 / 市场心理 / 关键价位"

    def __init__(self, llm_service):
        self._llm = llm_service
        self._prompts = {
            "stock_analysis": MarkdownChatPromptTemplate(
                "prompts/livermore/stock_analysis.md", caller_file=__file__
            ),
            # 其他 mode 可选，未实现的 mode 调用时抛 NotImplementedError
        }

    def analyze(self, context: PersonaContext) -> PersonaResult:
        if context.mode not in self._prompts:
            raise NotImplementedError(
                f"{self.name} 不支持 {context.mode} 模式"
            )
        ...
```

**步骤 2**：创建 prompt 文件

```
src/long_earn/skills/personas/prompts/livermore/stock_analysis.md
```

注册表自动发现（通过 `__init__.py` 的 import 触发 `@register` 装饰器）：

```python
# src/long_earn/skills/personas/__init__.py
from .buffett import BuffettPersona      # 触发 @register
from .charles_munger import CharlesMungerPersona
from .fiske import FiskePersona
from .petter import PetterPersona
from .livermore import LivermorePersona  # 新增
```

**无需修改任何子图拓扑或消费方代码**——注册表自动纳入新大师。

### F. 三消费方接入

#### 消费方 1：stock_analysis 子图（现有用法升级）

```python
# stock_analysis/subgraph.py
from long_earn.skills.personas import PersonaRegistry

def create_stock_analysis_subgraph(context):
    personas = PersonaRegistry.create_all(context.require_llm())

    def _buffett_node(state):
        result = personas["buffett"].analyze(PersonaContext(
            mode="stock_analysis",
            target=state["stock_data"],
            event_context=state.get("event_context", ""),
        ))
        return {"buffett_analysis": result.model_dump()}

    # 或直接用 partial 注入（ADR-002 模式）
    workflow.add_node("buffett", partial(_persona_node, persona=personas["buffett"]))
    ...
```

#### 消费方 2：strategy_rd 策略生成（新增）

```python
# strategy_rd/subgraph.py::_research_node
def _research_node(state, research_agent, personas, logger):
    # 策略生成前，征询大师视角补充投资逻辑
    master_views = {}
    for name, persona in personas.items():
        try:
            view = persona.analyze(PersonaContext(
                mode="strategy_generate",
                target={"query": state["query"], "knowledge": state.get("knowledge_context", "")},
            ))
            master_views[name] = view
        except NotImplementedError:
            continue  # 该大师不支持此 mode，跳过

    # 大师视角作为 hint 传入策略生成
    strategy = research_agent.research_strategy_with_context(
        state["query"],
        knowledge_context=state.get("knowledge_context", ""),
        master_hints=master_views,  # 新增参数
    )
    return {"strategy": strategy}
```

#### 消费方 3：strategy_rd 策略反思（新增）

```python
# strategy_rd/subgraph.py::_reflection_node
def _reflection_node(state, research_agent, personas, logger):
    # 反思时调用大师审视策略弱点
    master_perspectives = {}
    for name, persona in personas.items():
        try:
            view = persona.analyze(PersonaContext(
                mode="strategy_review",
                target=state["strategy"],
                backtest_result=state.get("backtest_result"),
            ))
            master_perspectives[name] = view
        except NotImplementedError:
            continue

    # 大师视角作为补充输入传入反思
    reflection = research_agent.reflect(
        state["strategy"],
        state["backtest_result"],
        master_perspectives=master_perspectives,  # 新增参数
    )
    return {"reflection": reflection}
```

### G. 文件结构

```
src/long_earn/skills/
├── __init__.py
└── personas/
    ├── __init__.py                    # 触发所有大师注册
    ├── protocol.py                    # MasterPersona Protocol + PersonaContext/Result
    ├── registry.py                    # PersonaRegistry 注册表
    ├── base.py                        # BasePersona 基类（可选，提供 _parse_result 等公用方法）
    ├── buffett.py                     # 4 个内置大师
    ├── charles_munger.py
    ├── fiske.py
    ├── petter.py
    └── prompts/                       # prompt 资产集中管理
        ├── buffett/
        │   ├── stock_analysis.md      # 股票分析模式 prompt
        │   ├── strategy_review.md     # 策略审视模式 prompt
        │   ├── strategy_generate.md   # 策略生成模式 prompt
        │   └── result_synthesis.md    # 结果综合模式 prompt
        ├── charles_munger/
        ├── fiske/
        └── petter/
```

### H. 与 ADR-002 节点注入的关系

ADR-002 用 `functools.partial` 注入依赖到模块级节点函数。本 ADR 的大师智能节点遵循同一模式：

```python
# 大师作为依赖注入到节点函数
def _buffett_node(state, persona: MasterPersona):
    return persona.analyze(PersonaContext(mode="stock_analysis", target=state["stock_data"]))

# partial 注入
workflow.add_node("buffett", partial(_buffett_node, persona=personas["buffett"]))
```

不引入新的注入机制，复用 ADR-002 的 partial 模式。

### I. 降级策略

大师调用失败时（LLM 异常 / 超时 / NotImplementedError）：
- **stock_analysis 模式**：该大师分析结果置空，其余大师结果正常汇总
- **strategy_generate / strategy_review 模式**：跳过该大师视角，不影响主流程
- **result_synthesis 模式**：降级为无大师视角的原始汇总

降级通过 try/except 实现，不中断主流程。

## 理由

1. **Protocol 统一契约**：`MasterPersona.analyze(context) -> result` 让所有消费方用相同方式调用，新增消费方零学习成本。
2. **注册表插件化**：`@PersonaRegistry.register` 让新增大师只需 1 个类 + N 个 prompt 文件，不动子图拓扑。
3. **多 mode 设计**：同一大师支持 4 种 mode，消费方按需选择，避免「巴菲特只能分析股票」的能力浪费。
4. **遵循 ADR-002**：partial 注入模式复用，不引入新机制。
5. **Prompt 集中管理**：`skills/personas/prompts/` 消除散落，4 个大师的 4 种 mode prompt 共 16 个文件，结构清晰。
6. **渐进式扩展**：大师可以只实现部分 mode（如新增大师只支持 stock_analysis），NotImplementedError 降级不影响其他消费方。

## 后果

### 正面

- **三消费方复用**：stock_analysis / strategy_rd research / strategy_rd reflection 统一调用大师能力
- **用户可扩展**：新增大师只需 1 类 + N prompt，注册表自动发现
- **Prompt 集中**：消除散落，维护成本下降
- **多 mode 复用**：同一大师服务多场景，投资回报率高

### 负面

- **大师仍是单次 LLM 调用**：本 ADR 不升级为 mini agent（多步推理），分析深度未提升。这是有意为之——先解决复用问题，再解决深度问题（未来可作为 ADR-013 升级）。
- **多 mode prompt 维护成本**：4 大师 × 4 mode = 16 个 prompt 文件，需逐个编写和测试。建议初期只实现 stock_analysis + strategy_review 两个 mode，其余按需扩展。
- **Registry 全局状态**：`PersonaRegistry` 是类变量全局注册，测试间需注意隔离（`_personas.clear()` 或测试 fixture 重置）。
- **strategy_rd 接口扩展**：`research_strategy_with_context` 和 `reflect` 需新增 `master_hints` / `master_perspectives` 参数，向后兼容（默认空 dict）。

## 实施计划

### 阶段 1：基础设施 + 4 大师迁移（stock_analysis mode）

- 新建 `src/long_earn/skills/personas/` 目录结构
- 实现 `protocol.py`（MasterPersona / PersonaContext / PersonaResult）
- 实现 `registry.py`（PersonaRegistry）
- 实现 `base.py`（BasePersona，含 `__init__` / `_parse_result` 等公用方法）
- 4 个大师从 `stock_analysis/agents/` 迁移到 `skills/personas/`，实现 `stock_analysis` mode
- prompt 从 `stock_analysis/agents/*.md` 迁移到 `skills/personas/prompts/*/stock_analysis.md`
- few-shot 从各 agent 的 `EXAMPLES` 迁移到大师类内部
- `stock_analysis/subgraph.py` 改为通过 `PersonaRegistry.create_all()` 获取大师实例
- 测试：4 大师分析结果与迁移前一致
- **提交 1**

### 阶段 2：strategy_review mode（策略反思接入）

- 为 4 个大师各新增 `strategy_review` mode prompt
- `StrategyResearchAgent.reflect` 新增 `master_perspectives` 参数（默认空 dict，向后兼容）
- `strategy_rd/subgraph.py::_reflection_node` 调用大师 `strategy_review` mode
- 测试：反思结果含大师视角，无大师时降级为原行为
- **提交 2**

### 阶段 3：strategy_generate mode（策略生成接入）

- 为 4 个大师各新增 `strategy_generate` mode prompt
- `StrategyResearchAgent.research_strategy_with_context` 新增 `master_hints` 参数
- `strategy_rd/subgraph.py::_research_node` 调用大师 `strategy_generate` mode
- 测试：策略生成含大师投资逻辑 hint
- **提交 3**

### 阶段 4：扩展性验证

- 新增 1 个示例大师（如 `livermore`）验证扩展流程
- 编写扩展文档：如何新增大师（1 类 + N prompt + `__init__.py` import）
- 测试：新大师自动注册，3 个消费方均可调用
- **提交 4**

## 与其他 ADR 的关系

- **ADR-002**（partial 节点注入）：大师智能节点通过 partial 注入到子图节点函数，复用同一模式。
- **ADR-011**（jinja2 + ChatPromptTemplate）：大师 prompt 继续用 `MarkdownChatPromptTemplate` + jinja2 `{{ var }}`，本 ADR 是 prompt 资产的消费方重组，不改变渲染层。
- **ADR-010**（HTR 假设树）：strategy_rd reflection 节点若调用大师 `strategy_review` mode，是 HTR 反思的增强，不改变 HTR 拓扑。HTR 的 `observe` 节点也可选调用大师视角补充观察。
- **ADR-009**（算子目录）：`operator_dev` 的 Spec + Registry 模式是本 ADR 注册表的设计参考，但大师是同步调用的，不需要 Backlog 队列。

## 未来演进（非本 ADR 范围）

- **ADR-013（潜在）：大师 mini agent 升级**：将大师从单次 LLM 调用升级为多步推理子图（observe→analyze→validate→conclude），支持工具调用。实施前需做准确性归因分析，判断瓶颈是否在推理深度。
- **动态大师选择**：根据用户查询语义自动选择最相关的 K 个大师（类似 `SemanticSimilarityExampleSelector`），而非每次调用全部 4 个。
- **大师共识算法**：多个大师结果冲突时，用投票或加权机制产出共识结论。

## 参考文献

- ADR-002 partial 节点注入: `docs/adr/002-partial-node-injection.md`
- ADR-010 HTR 假设树: `docs/adr/010-hypothesis-tree-refinement.md`
- ADR-011 jinja2 + ChatPromptTemplate: `docs/adr/011-unified-mustache-prompt-templating.md`
- LangChain Protocol 模式: https://docs.python.org/3/library/typing.html#typing.Protocol
- 结构化输出（Pydantic）: https://python.langchain.com/docs/how_to/structured_output/
