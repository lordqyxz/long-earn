"""策略研究提示词模块

提供策略研究、优化等场景的提示词模板。
"""

from langchain_core.prompts import PromptTemplate

from long_earn.core.prompt_loader import MarkdownPromptTemplate

# 从 Markdown 文件加载策略研究提示词模板
_research_prompt_template = MarkdownPromptTemplate(
    "strategy_research_prompt.md",
    ["target_market", "query", "strategy_examples", "strategy_context"],
    __file__,
)


def create_strategy_research_prompt(
    target_market: str, query: str, strategy_examples: str, strategy_context: str
) -> str:
    """创建策略研究提示词

    Args:
        target_market: 目标市场（stock/future/crypto）
        query: 用户查询/需求
        strategy_examples: 历史成功策略参考
        strategy_context: 当前策略上下文

    Returns:
        格式化后的提示词字符串
    """
    return _research_prompt_template.format(
        target_market=target_market,
        query=query,
        strategy_examples=strategy_examples,
        strategy_context=strategy_context,
    )


# 策略优化提示词 - 内联定义（无对应 md 文件）
strategy_optimize_prompt = PromptTemplate(
    input_variables=[
        "strategy",
        "suggestions_text",
        "backtest_history",
        "market_characteristics",
    ],
    template="""你是一位世界顶级的量化策略优化专家。请根据改进建议优化当前策略。

## 当前策略
{strategy}

## 改进建议
{suggestions_text}

## 历史回测结果
{backtest_history}

## 市场特征
{market_characteristics}

## 优化要求
1. 针对改进建议中的每个问题，给出具体的优化方案
2. 优化后的策略必须保持逻辑清晰可解释
3. 必须包含具体的风险控制措施
4. 避免过拟合，考虑样本外表现

## 输出格式
请严格按照以下 JSON 格式返回优化后的策略：
```json
{{
    "strategy_name": "优化后的策略名称",
    "strategy_type": "策略类型",
    "rationale": "优化理由",
    "investment_logic": "优化后的投资逻辑",
    "factors_used": [],
    "position_management": {{}},
    "risk_control": {{}},
    "backtest_params": {{}},
    "expected_metrics": {{}},
    "potential_risks": [],
    "improvement_directions": []
}}
```
""",
)
