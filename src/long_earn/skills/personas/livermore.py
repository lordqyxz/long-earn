"""杰西·利弗莫尔大师 Persona — ADR-012 Phase 4

扩展性验证示例大师：展示新增一位大师只需 1 个类 + 3 个 prompt + __init__.py import。
通过 @PersonaRegistry.register 自动注册，被 stock_analysis / strategy_rd research /
strategy_rd reflection 三个消费方按 name 调用。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from long_earn.skills.personas.base import BasePersona
from long_earn.skills.personas.protocol import PersonaContext, PersonaResult
from long_earn.skills.personas.registry import PersonaRegistry

# Few-shot 示例（stock_analysis 模式）
EXAMPLES = [
    HumanMessage(content="分析贵州茅台的投资价值"),
    AIMessage(
        content=(
            "对于茅台这样的标的，我关注的是价格趋势是否确认了基本面的强势。"
            "不要在盘整时买入，而要等待它突破前期高点、伴随成交量放大时再进场——"
            "这才是关键点。一旦趋势确立，顺势加仓；若突破失败，立即止损离场。"
        )
    ),
    HumanMessage(content="分析周期股的投资价值"),
    AIMessage(
        content=(
            "周期股的关键在于捕捉主升浪的起点。当行业景气从谷底反转、"
            "价格突破关键阻力位时，是较好的介入时机。但必须设定明确止损，"
            "因为周期反转往往迅速而剧烈，逆势持仓会造成重大损失。"
        )
    ),
    HumanMessage(content="分析科技成长股的投资价值"),
    AIMessage(
        content=(
            "对于科技成长股，我关注价格是否反映了市场预期。"
            "若价格持续创新高且成交量配合，说明主力资金在吸纳，可顺势跟进。"
            "但成长股波动剧烈，必须严格执行止损，不让盈利回吐为亏损。"
        )
    ),
]

# strategy_review 模式 few-shot 示例
STRATEGY_REVIEW_EXAMPLES = [
    HumanMessage(
        content=(
            "策略详情：{\"strategy_name\": \"趋势突破选股\", "
            "\"factors\": [\"breakout_20d_high\", \"volume_surge>1.5x\"], "
            "\"holding\": \"直到跌破 10 日均线\"}\n"
            "回测结果：{\"total_return\": 0.55, \"max_drawdown\": 0.22, "
            "\"sharpe_ratio\": 1.1}\n市场事件上下文：牛市初期"
        )
    ),
    AIMessage(
        content=(
            "{\"verdict\": \"接受\", "
            "\"rationale\": \"策略基于趋势突破与成交量确认，符合顺势交易原则；"
            "回撤可控、夏普比率合理，具备趋势跟踪的纪律性。\", "
            "\"weaknesses\": [\"突破失败时的止损规则未明确\"], "
            "\"suggestions\": [\"加入 ATR 动态止损\", \"突破失败立即离场不补仓\"], "
            "\"confidence\": 0.75}"
        )
    ),
    HumanMessage(
        content=(
            "策略详情：{\"strategy_name\": \"逆势抄底\", "
            "\"factors\": [\"drawdown>30%\", \"pe<historical_low\"], "
            "\"holding\": \"长期\"}\n"
            "回测结果：{\"total_return\": -0.15, \"max_drawdown\": 0.45, "
            "\"sharpe_ratio\": -0.3}\n市场事件上下文：熊市中段"
        )
    ),
    AIMessage(
        content=(
            "{\"verdict\": \"拒绝\", "
            "\"rationale\": \"逆势抄底违背顺势交易原则，在熊市中段抄底"
            "等同于接飞刀，回撤失控且收益为负，缺乏纪律性。\", "
            "\"weaknesses\": [\"逆势交易\", \"无止损\", \"抄底逻辑主观\"], "
            "\"suggestions\": [\"改为等待趋势反转确认后再进场\", "
            "\"加入均线多头排列过滤\", \"设置硬止损\"], \"confidence\": 0.8}"
        )
    ),
]

# strategy_generate 模式 few-shot 示例
STRATEGY_GENERATE_EXAMPLES = [
    HumanMessage(
        content=(
            "用户查询：研究一个趋势跟踪选股策略\n"
            "已有知识上下文：市场处于多头行情，成交活跃"
        )
    ),
    AIMessage(
        content=(
            "{\"verdict\": \"推荐\", "
            "\"rationale\": \"趋势跟踪契合顺势交易原则，多头行情下"
            "突破策略胜率较高；关注关键点突破与成交量确认。\", "
            "\"suggestions\": [\"以 20 日新高突破 + 成交量放大 1.5 倍为入场信号\", "
            "\"跌破 10 日均线止损\", \"盈利后金字塔加仓\"], "
            "\"confidence\": 0.8}"
        )
    ),
    HumanMessage(
        content=(
            "用户查询：研究一个逆势抄底策略\n"
            "已有知识上下文：市场处于熊市中段"
        )
    ),
    AIMessage(
        content=(
            "{\"verdict\": \"不推荐\", "
            "\"rationale\": \"逆势抄底违背顺势交易原则，熊市中段抄底"
            "风险极高，容易造成重大损失。\", "
            "\"suggestions\": [\"改为等待趋势反转确认后再进场\", "
            "\"关注均线多头排列与成交量回升\", \"熊市优先考虑空仓或对冲\"], "
            "\"confidence\": 0.85}"
        )
    ),
]


@PersonaRegistry.register
class LivermorePersona(BasePersona):
    """杰西·利弗莫尔视角的大师 Persona。

    扩展性验证示例（ADR-012 Phase 4）：展示新增大师的标准流程。
    """

    name = "livermore"
    display_name = "杰西·利弗莫尔"
    perspective = "趋势交易"
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
        """stock_analysis 模式：加载 livermore/stock_analysis.md，调用 LLM。"""
        prompt = self._load_prompt("stock_analysis")
        messages = prompt.format_messages(
            stock_data=context.target,
            event_context=context.event_context,
            examples=self.examples,
        )
        response = self.llm.invoke(messages)
        return self._parse_result(response, "stock_analysis")

    def _review_strategy(self, context: PersonaContext) -> PersonaResult:
        """strategy_review 模式：加载 livermore/strategy_review.md，调用 LLM。"""
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
        """strategy_generate 模式：加载 livermore/strategy_generate.md，调用 LLM。"""
        prompt = self._load_prompt("strategy_generate")
        target = context.target or {}
        messages = prompt.format_messages(
            query=target.get("query", ""),
            knowledge_context=target.get("knowledge_context", ""),
            examples=self.strategy_generate_examples,
        )
        response = self.llm.invoke(messages)
        return self._parse_result(response, "strategy_generate")
