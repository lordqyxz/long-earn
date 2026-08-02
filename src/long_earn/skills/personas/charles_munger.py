"""查理芒格大师 Persona — ADR-012 Phase 1

从 stock_analysis/agents/charles_munger_analyst.py 迁移而来，
继承 BasePersona，通过 @PersonaRegistry.register 自动注册。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from long_earn.skills.personas.base import BasePersona
from long_earn.skills.personas.protocol import PersonaContext, PersonaResult
from long_earn.skills.personas.registry import PersonaRegistry

# Few-shot 示例（从原 charles_munger_analyst.py 迁移）
EXAMPLES = [
    HumanMessage(content="分析沃尔玛的投资价值"),
    AIMessage(
        content=(
            "从心理学角度看，沃尔玛利用消费者的价格敏感性；"
            "从经济学角度看，规模经济形成成本优势；"
            "从工程学角度看，供应链管理系统是关键杠杆。"
        )
    ),
    HumanMessage(content="分析互联网平台的投资价值"),
    AIMessage(
        content=(
            "从网络效应（数学模型）看，平台价值随用户增加而指数级增长；"
            "从心理学角度看，用户粘性形成行为惯性；"
            "从生物学角度看，平台生态系统的适应性。"
        )
    ),
    HumanMessage(content="分析制造业企业的投资价值"),
    AIMessage(
        content=(
            "从物理学角度看，生产效率提升有惯性；"
            "从工程学角度看，自动化是系统优化的关键；"
            "从经济学角度看，成本结构决定了盈利能力。"
        )
    ),
]

# strategy_review 模式 few-shot 示例
STRATEGY_REVIEW_EXAMPLES = [
    HumanMessage(
        content=(
            '策略详情：{"strategy_name": "多因子动量+价值", '
            '"factors": ["mom_20d", "bm_ratio"], '
            '"rebalance": "weekly"}\n'
            '回测结果：{"total_return": 0.5, "max_drawdown": 0.22, '
            '"sharpe_ratio": 1.0}\n市场事件上下文：无'
        )
    ),
    AIMessage(
        content=(
            '{"verdict": "改进", '
            '"rationale": "从经济学看，价值+动量组合机会成本合理；'
            "从心理学看，周度调仓易受近期偏误驱动；"
            '从数学概率看，回撤表明分布尾部未充分控制。", '
            '"weaknesses": ["周度调仓存在近期偏误", '
            '"尾部风险控制不足", "样本期可能存在幸存者偏差"], '
            '"suggestions": ["延长调仓周期至月度", "加入波动率过滤", '
            '"做样本外稳健性检验"], "confidence": 0.75}'
        )
    ),
    HumanMessage(
        content=(
            '策略详情：{"strategy_name": "高杠杆趋势跟踪", '
            '"leverage": 3, "factors": ["trend_60d"]}\n'
            '回测结果：{"total_return": 1.2, "max_drawdown": 0.55, '
            '"sharpe_ratio": 0.8}\n市场事件上下文：无'
        )
    ),
    AIMessage(
        content=(
            '{"verdict": "拒绝", '
            '"rationale": "从工程学看，3 倍杠杆放大反馈循环风险；'
            "从心理学看，高收益诱发过度自信偏差；"
            '从生物学看，策略对环境变化缺乏适应性。", '
            '"weaknesses": ["杠杆失控", "过度自信偏差", '
            '"回撤接近不可恢复阈值"], '
            '"suggestions": ["降低杠杆至 1 倍", "加入动态仓位管理", '
            '"增加压力测试"], "confidence": 0.9}'
        )
    ),
]

# strategy_generate 模式 few-shot 示例
STRATEGY_GENERATE_EXAMPLES = [
    HumanMessage(
        content=(
            "用户查询：研究一个多因子价值+动量选股策略\n"
            "已有知识上下文：市场风格切换频繁，价值与动量因子存在轮动效应"
        )
    ),
    AIMessage(
        content=(
            '{"verdict": "推荐", '
            '"rationale": "从经济学看，价值+动量组合机会成本合理；'
            "从心理学看，可利用市场近期偏误；"
            '从概率论看，多因子分散降低单一因子失效风险。", '
            '"suggestions": ["延长调仓周期至月度以避免近期偏误", '
            '"加入波动率过滤控制尾部风险", "做样本外稳健性检验"], '
            '"confidence": 0.75}'
        )
    ),
    HumanMessage(content=("用户查询：研究一个高杠杆趋势跟踪策略\n已有知识上下文：无")),
    AIMessage(
        content=(
            '{"verdict": "不推荐", '
            '"rationale": "从工程学看，高杠杆放大反馈循环风险；'
            "从心理学看，诱发过度自信偏差；"
            '从生物学看，策略对环境变化缺乏适应性。", '
            '"suggestions": ["降低杠杆至 1 倍", "加入动态仓位管理", '
            '"增加压力测试与极端场景检验"], "confidence": 0.9}'
        )
    ),
]


@PersonaRegistry.register
class CharlesMungerPersona(BasePersona):
    """查理芒格视角的大师 Persona。"""

    name = "charles_munger"
    display_name = "查理·芒格"
    perspective = "多学科思维模型"
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
        """stock_analysis 模式：加载 charles_munger/stock_analysis.md，调用 LLM。"""
        prompt = self._load_prompt("stock_analysis")
        messages = prompt.format_messages(
            stock_data=context.target,
            event_context=context.event_context,
            examples=self.examples,
        )
        response = self.llm.invoke(messages)
        return self._parse_result(response, "stock_analysis")

    def _review_strategy(self, context: PersonaContext) -> PersonaResult:
        """strategy_review 模式：加载 charles_munger/strategy_review.md，调用 LLM。"""
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
        """strategy_generate 模式：加载 charles_munger/strategy_generate.md，调用 LLM。"""
        prompt = self._load_prompt("strategy_generate")
        target = context.target or {}
        messages = prompt.format_messages(
            query=target.get("query", ""),
            knowledge_context=target.get("knowledge_context", ""),
            examples=self.strategy_generate_examples,
        )
        response = self.llm.invoke(messages)
        return self._parse_result(response, "strategy_generate")
