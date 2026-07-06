"""费雪大师 Persona — ADR-012 Phase 1

从 stock_analysis/agents/fiske_analyst.py 迁移而来，
继承 BasePersona，通过 @PersonaRegistry.register 自动注册。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from long_earn.skills.personas.base import BasePersona
from long_earn.skills.personas.protocol import PersonaContext, PersonaResult
from long_earn.skills.personas.registry import PersonaRegistry

# Few-shot 示例（从原 fiske_analyst.py 迁移）
EXAMPLES = [
    HumanMessage(content="分析半导体公司的投资价值"),
    AIMessage(
        content=(
            "对于半导体公司，我们重点考察其研发投入占营收比例、专利数量、"
            "技术代差优势以及下游需求增长趋势。高研发投入通常预示着未来的竞争优势。"
        )
    ),
    HumanMessage(content="分析生物制药公司的投资价值"),
    AIMessage(
        content=(
            "对于生物制药公司，我们关注其在研管线丰富程度、临床试验进展、"
            "监管审批预期以及专利保护期。创新药的成功率虽低，但一旦成功回报巨大。"
        )
    ),
    HumanMessage(content="分析电动车制造商的投资价值"),
    AIMessage(
        content=(
            "对于电动车制造商，我们评估其电池技术先进性、产能扩张计划、"
            "品牌认知度以及充电网络布局。技术领先和规模效应是关键。"
        )
    ),
]

# strategy_review 模式 few-shot 示例
STRATEGY_REVIEW_EXAMPLES = [
    HumanMessage(
        content=(
            "策略详情：{\"strategy_name\": \"高研发投入选股\", "
            "\"factors\": [\"rd_ratio>8%\", \"revenue_growth>15%\"], "
            "\"rebalance\": \"quarterly\"}\n"
            "回测结果：{\"total_return\": 0.7, \"max_drawdown\": 0.25, "
            "\"sharpe_ratio\": 1.3}\n市场事件上下文：产业政策利好"
        )
    ),
    AIMessage(
        content=(
            "{\"verdict\": \"接受\", "
            "\"rationale\": \"策略聚焦高研发投入与营收成长，"
            "契合成长股投资原则；技术壁垒与创新能力被有效捕捉。\", "
            "\"weaknesses\": [\"未考虑专利到期风险\", "
            "\"行业景气度下行缺乏防御机制\"], "
            "\"suggestions\": [\"加入专利剩余年限因子\", "
            "\"增加行业景气度过滤\"], \"confidence\": 0.8}"
        )
    ),
    HumanMessage(
        content=(
            "策略详情：{\"strategy_name\": \"低估值蓝筹\", "
            "\"factors\": [\"pe<8\", \"dividend_yield>5%\"], "
            "\"rebalance\": \"annual\"}\n"
            "回测结果：{\"total_return\": 0.3, \"max_drawdown\": 0.15, "
            "\"sharpe_ratio\": 0.9}\n市场事件上下文：无"
        )
    ),
    AIMessage(
        content=(
            "{\"verdict\": \"改进\", "
            "\"rationale\": \"策略与成长性无关，仅关注估值与分红，"
            "未捕捉研发投入与技术壁垒，成长股原则覆盖不足。\", "
            "\"weaknesses\": [\"缺乏成长性因子\", \"研发投入缺失\", "
            "\"技术壁垒未考量\"], "
            "\"suggestions\": [\"加入营收增长率因子\", "
            "\"纳入研发占比过滤\", \"增加技术代差评估\"], "
            "\"confidence\": 0.7}"
        )
    ),
]

# strategy_generate 模式 few-shot 示例
STRATEGY_GENERATE_EXAMPLES = [
    HumanMessage(
        content=(
            "用户查询：研究一个高研发投入成长股选股策略\n"
            "已有知识上下文：产业政策利好半导体与新能源，研发投入领先企业增多"
        )
    ),
    AIMessage(
        content=(
            "{\"verdict\": \"推荐\", "
            "\"rationale\": \"高研发投入契合成长股投资原则，"
            "技术壁垒与创新能力被有效捕捉，产业政策强化成长持续性。\", "
            "\"suggestions\": [\"以研发占比>8% 与营收增长>15% 双因子筛选\", "
            "\"加入专利剩余年限因子\", \"增加行业景气度过滤\"], "
            "\"confidence\": 0.8}"
        )
    ),
    HumanMessage(
        content=(
            "用户查询：研究一个低估值蓝筹分红策略\n"
            "已有知识上下文：无"
        )
    ),
    AIMessage(
        content=(
            "{\"verdict\": \"不推荐\", "
            "\"rationale\": \"低估值蓝筹分红策略与成长性无关，"
            "未捕捉研发投入与技术壁垒，成长股原则覆盖不足。\", "
            "\"suggestions\": [\"加入营收增长率因子\", "
            "\"纳入研发占比过滤\", \"增加技术代差评估\"], "
            "\"confidence\": 0.7}"
        )
    ),
]


@PersonaRegistry.register
class FiskePersona(BasePersona):
    """费雪视角的大师 Persona。"""

    name = "fiske"
    display_name = "菲利普·费雪"
    perspective = "成长股投资"
    supported_modes = ("stock_analysis", "strategy_review", "strategy_generate")

    def __init__(self, llm) -> None:
        super().__init__(llm)
        self.examples = EXAMPLES
        self.strategy_review_examples = STRATEGY_REVIEW_EXAMPLES
        self.strategy_generate_examples = STRATEGY_GENERATE_EXAMPLES

    def _do_analyze(self, context: PersonaContext) -> PersonaResult:
        """派发到对应 mode 的分析逻辑。"""
        if context.mode == "stock_analysis":
            return self._analyze_stock(context)
        elif context.mode == "strategy_review":
            return self._review_strategy(context)
        elif context.mode == "strategy_generate":
            return self._generate_strategy(context)
        raise NotImplementedError(f"{self.name} 不支持 {context.mode}")

    def _analyze_stock(self, context: PersonaContext) -> PersonaResult:
        """stock_analysis 模式：加载 fiske/stock_analysis.md，调用 LLM。"""
        prompt = self._load_prompt("stock_analysis")
        messages = prompt.format_messages(
            stock_data=context.target,
            event_context=context.event_context,
            examples=self.examples,
        )
        response = self.llm.invoke(messages)
        return self._parse_result(response, "stock_analysis")

    def _review_strategy(self, context: PersonaContext) -> PersonaResult:
        """strategy_review 模式：加载 fiske/strategy_review.md，调用 LLM。"""
        prompt = self._load_prompt("strategy_review")
        messages = prompt.format_messages(
            strategy=context.target,
            backtest_result=context.backtest_result or {},
            event_context=context.event_context,
            examples=self.strategy_review_examples,
        )
        response = self.llm.invoke(messages)
        return self._parse_result(response, "strategy_review")

    def _generate_strategy(self, context: PersonaContext) -> PersonaResult:
        """strategy_generate 模式：加载 fiske/strategy_generate.md，调用 LLM。"""
        prompt = self._load_prompt("strategy_generate")
        target = context.target or {}
        messages = prompt.format_messages(
            query=target.get("query", ""),
            knowledge_context=target.get("knowledge_context", ""),
            examples=self.strategy_generate_examples,
        )
        response = self.llm.invoke(messages)
        return self._parse_result(response, "strategy_generate")
