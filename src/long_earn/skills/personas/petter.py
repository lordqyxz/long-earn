"""彼得林奇大师 Persona — ADR-012 Phase 1

从 stock_analysis/agents/petter_analyst.py 迁移而来，
继承 BasePersona，通过 @PersonaRegistry.register 自动注册。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from long_earn.skills.personas.base import BasePersona
from long_earn.skills.personas.protocol import PersonaContext, PersonaResult
from long_earn.skills.personas.registry import PersonaRegistry

# Few-shot 示例（从原 petter_analyst.py 迁移）
EXAMPLES = [
    HumanMessage(content="分析沃尔玛的投资价值"),
    AIMessage(
        content=(
            "对于沃尔玛这类稳定增长型股票，我们关注其同店销售额增长、门店扩张计划和成本控制能力。"
            "PEG 比率适中，适合长期持有。"
        )
    ),
    HumanMessage(content="分析高通的投资价值"),
    AIMessage(
        content=(
            "对于高通这类快速成长型股票，我们评估其在 5G、芯片设计领域的技术领先地位，"
            "以及研发投入与收入比。虽然估值较高，但增长潜力巨大。"
        )
    ),
    HumanMessage(content="分析房地产信托基金的投资价值"),
    AIMessage(
        content=(
            "对于房地产信托基金 (REITs)，我们将其归类为缓慢增长型，"
            "重点关注租金收益率、物业组合质量和债务结构。适合追求稳定分红的投资者。"
        )
    ),
]

# strategy_review 模式 few-shot 示例
STRATEGY_REVIEW_EXAMPLES = [
    HumanMessage(
        content=(
            "策略详情：{\"strategy_name\": \"PEG 选股\", "
            "\"factors\": [\"peg<1\", \"earnings_growth>20%\"], "
            "\"classification\": \"fast_grower\", \"rebalance\": \"monthly\"}\n"
            "回测结果：{\"total_return\": 0.8, \"max_drawdown\": 0.2, "
            "\"sharpe_ratio\": 1.4}\n市场事件上下文：无"
        )
    ),
    AIMessage(
        content=(
            "{\"verdict\": \"接受\", "
            "\"rationale\": \"策略以 PEG<1 配合高成长性选股，"
            "估值与成长性匹配合理，分类为快速增长型适配正确。\", "
            "\"weaknesses\": [\"成长性定义单一，未考虑盈利质量\"], "
            "\"suggestions\": [\"加入经营性现金流增长率\", "
            "\"区分内生性与外延式增长\"], \"confidence\": 0.85}"
        )
    ),
    HumanMessage(
        content=(
            "策略详情：{\"strategy_name\": \"高 PE 投机\", "
            "\"factors\": [\"pe>100\", \"momentum_strong\"], "
            "\"rebalance\": \"weekly\"}\n"
            "回测结果：{\"total_return\": 0.4, \"max_drawdown\": 0.4, "
            "\"sharpe_ratio\": 0.5}\n市场事件上下文：无"
        )
    ),
    AIMessage(
        content=(
            "{\"verdict\": \"拒绝\", "
            "\"rationale\": \"PE>100 与 PEG 成长投资原则严重背离，"
            "估值过高且未做分类适配，回撤过大风险不可控。\", "
            "\"weaknesses\": [\"估值严重偏离合理区间\", "
            "\"无 PEG 匹配\", \"分类适配缺失\", \"回撤失控\"], "
            "\"suggestions\": [\"改为 PEG<1.5 选股\", \"加入林奇六类分类\", "
            "\"设置最大回撤阈值\"], \"confidence\": 0.9}"
        )
    ),
]

# strategy_generate 模式 few-shot 示例
STRATEGY_GENERATE_EXAMPLES = [
    HumanMessage(
        content=(
            "用户查询：研究一个 PEG 选股策略\n"
            "已有知识上下文：成长股盈利预期改善，市场关注估值与成长匹配"
        )
    ),
    AIMessage(
        content=(
            "{\"verdict\": \"推荐\", "
            "\"rationale\": \"PEG 选股契合成长投资原则，"
            "估值与成长性匹配合理，分类为快速增长型适配正确。\", "
            "\"suggestions\": [\"以 PEG<1 与盈利增长>20% 双因子筛选\", "
            "\"加入林奇六类分类标签\", \"区分内生性与外延式增长\"], "
            "\"confidence\": 0.85}"
        )
    ),
    HumanMessage(
        content=(
            "用户查询：研究一个高 PE 投机策略\n"
            "已有知识上下文：无"
        )
    ),
    AIMessage(
        content=(
            "{\"verdict\": \"不推荐\", "
            "\"rationale\": \"PE>100 与 PEG 成长投资原则严重背离，"
            "估值过高且未做分类适配，回撤过大风险不可控。\", "
            "\"suggestions\": [\"改为 PEG<1.5 选股\", \"加入林奇六类分类\", "
            "\"设置最大回撤阈值\"], \"confidence\": 0.9}"
        )
    ),
]


@PersonaRegistry.register
class PetterPersona(BasePersona):
    """彼得林奇视角的大师 Persona。"""

    name = "petter"
    display_name = "彼得·林奇"
    perspective = "PEG 成长投资"
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
        """stock_analysis 模式：加载 petter/stock_analysis.md，调用 LLM。"""
        prompt = self._load_prompt("stock_analysis")
        messages = prompt.format_messages(
            stock_data=context.target,
            event_context=context.event_context,
            examples=self.examples,
        )
        response = self.llm.invoke(messages)
        return self._parse_result(response, "stock_analysis")

    def _review_strategy(self, context: PersonaContext) -> PersonaResult:
        """strategy_review 模式：加载 petter/strategy_review.md，调用 LLM。"""
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
        """strategy_generate 模式：加载 petter/strategy_generate.md，调用 LLM。"""
        prompt = self._load_prompt("strategy_generate")
        target = context.target or {}
        messages = prompt.format_messages(
            query=target.get("query", ""),
            knowledge_context=target.get("knowledge_context", ""),
            examples=self.strategy_generate_examples,
        )
        response = self.llm.invoke(messages)
        return self._parse_result(response, "strategy_generate")
