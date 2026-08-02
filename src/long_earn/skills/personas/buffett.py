"""巴菲特大师 Persona — ADR-012 Phase 1

从 stock_analysis/agents/buffett_analyst.py 迁移而来，
继承 BasePersona，通过 @PersonaRegistry.register 自动注册。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from long_earn.skills.personas.base import BasePersona
from long_earn.skills.personas.protocol import PersonaContext, PersonaResult
from long_earn.skills.personas.registry import PersonaRegistry

# Few-shot 示例（从原 buffett_analyst.py 迁移）
EXAMPLES = [
    HumanMessage(content="分析可口可乐的投资价值"),
    AIMessage(
        content=(
            "对于可口可乐这样的消费品牌，我们关注其全球品牌影响力、定价权和稳定的现金流。"
            "即使在经济衰退期间，消费者仍会购买这些必需品，这构成了强大的护城河。"
        )
    ),
    HumanMessage(content="分析银行股的投资价值"),
    AIMessage(
        content=(
            "对于银行股，我们重点关注资产质量、净息差和风险管理能力。"
            "优秀的银行能够在控制风险的同时获得稳定收益。"
        )
    ),
    HumanMessage(content="分析科技公司的投资价值"),
    AIMessage(
        content=(
            "对于科技公司，我们评估其技术壁垒、市场占有率和创新持续性。"
            "像苹果这样的公司不仅有强大的品牌，还有生态系统锁定效应。"
        )
    ),
]

# strategy_review 模式 few-shot 示例
STRATEGY_REVIEW_EXAMPLES = [
    HumanMessage(
        content=(
            '策略详情：{"strategy_name": "低 PE 蓝筹选股", '
            '"factors": ["pe<10", "roe>15%"], "rebalance": "monthly"}\n'
            '回测结果：{"total_return": 0.45, "max_drawdown": 0.18, '
            '"sharpe_ratio": 1.2}\n市场事件上下文：央行降息周期'
        )
    ),
    AIMessage(
        content=(
            '{"verdict": "接受", '
            '"rationale": "策略聚焦低估值高 ROE 蓝筹，符合价值投资原则；'
            '回测显示稳健的夏普比率与可控回撤，护城河与安全边际兼顾。", '
            '"weaknesses": ["PE 阈值偏静态，未区分行业估值中枢差异"], '
            '"suggestions": ["按行业分位调整 PE 阈值", "增加现金流稳定性过滤"], '
            '"confidence": 0.8}'
        )
    ),
    HumanMessage(
        content=(
            '策略详情：{"strategy_name": "短线动量追涨", '
            '"factors": ["5d_return>10%"], "holding": "3天"}\n'
            '回测结果：{"total_return": 0.6, "max_drawdown": 0.35, '
            '"sharpe_ratio": 0.4}\n市场事件上下文：无'
        )
    ),
    AIMessage(
        content=(
            '{"verdict": "拒绝", '
            '"rationale": "策略依赖短期价格动量而非企业内在价值，'
            '回撤过大存在永久性资本损失风险，违背价值投资原则。", '
            '"weaknesses": ["无护城河考量", "回撤失控", "换手过高"], '
            '"suggestions": ["改为长期持有逻辑", "加入基本面质量过滤", '
            '"设置最大回撤止损"], "confidence": 0.85}'
        )
    ),
]

# strategy_generate 模式 few-shot 示例
STRATEGY_GENERATE_EXAMPLES = [
    HumanMessage(
        content=(
            "用户查询：研究一个基于低估值蓝筹的选股策略\n"
            "已有知识上下文：央行降息周期，蓝筹股估值处于历史低位"
        )
    ),
    AIMessage(
        content=(
            '{"verdict": "推荐", '
            '"rationale": "低估值蓝筹契合价值投资原则，'
            '降息周期有利于估值修复；关注 ROE 与护城河可持续性。", '
            '"suggestions": ["以 ROE>15% 与 PE<10 双因子筛选", '
            '"加入自由现金流稳定性过滤", "月度调仓、集中持有优质标的"], '
            '"confidence": 0.8}'
        )
    ),
    HumanMessage(content=("用户查询：研究一个短线动量追涨策略\n已有知识上下文：无")),
    AIMessage(
        content=(
            '{"verdict": "不推荐", '
            '"rationale": "短线动量追涨依赖价格博弈而非企业内在价值，'
            '存在永久性资本损失风险，违背价值投资原则。", '
            '"suggestions": ["改为长期持有逻辑", "加入基本面质量过滤", '
            '"关注护城河与安全边际"], "confidence": 0.85}'
        )
    ),
]


@PersonaRegistry.register
class BuffettPersona(BasePersona):
    """巴菲特视角的大师 Persona。"""

    name = "buffett"
    display_name = "沃伦·巴菲特"
    perspective = "价值投资"
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
        """stock_analysis 模式：加载 buffett/stock_analysis.md，调用 LLM。"""
        prompt = self._load_prompt("stock_analysis")
        messages = prompt.format_messages(
            stock_data=context.target,
            event_context=context.event_context,
            examples=self.examples,
        )
        response = self.llm.invoke(messages)
        return self._parse_result(response, "stock_analysis")

    def _review_strategy(self, context: PersonaContext) -> PersonaResult:
        """strategy_review 模式：加载 buffett/strategy_review.md，调用 LLM。"""
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
        """strategy_generate 模式：加载 buffett/strategy_generate.md，调用 LLM。"""
        prompt = self._load_prompt("strategy_generate")
        target = context.target or {}
        messages = prompt.format_messages(
            query=target.get("query", ""),
            knowledge_context=target.get("knowledge_context", ""),
            examples=self.strategy_generate_examples,
        )
        response = self.llm.invoke(messages)
        return self._parse_result(response, "strategy_generate")
