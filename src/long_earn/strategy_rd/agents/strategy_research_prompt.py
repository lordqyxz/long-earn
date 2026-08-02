"""策略研究提示词模块

提供策略研究、优化等场景的提示词模板。
策略优化 prompt 已迁移为 .md 文件（strategy_optimize_prompt.md），
消除内联 Python 字符串中的退役语法引用（ADR-016 阶段 4）。
"""

from __future__ import annotations

from typing import Any

from long_earn.core.prompt_loader import MarkdownPromptTemplate

_research_prompt_template = MarkdownPromptTemplate(
    "strategy_research_prompt.md",
    [
        "target_market",
        "query",
        "strategy_examples",
        "strategy_context",
        "master_hints_context",
    ],
    __file__,
)

_optimize_prompt_template = MarkdownPromptTemplate(
    "strategy_optimize_prompt.md",
    [
        "strategy",
        "suggestions_text",
        "backtest_history",
        "market_characteristics",
        "operator_catalog",
    ],
    __file__,
)


def create_strategy_research_prompt(
    target_market: str,
    query: str,
    strategy_examples: str,
    strategy_context: str,
    master_hints_context: str = "",
) -> str:
    """创建策略研究提示词

    Args:
        target_market: 目标市场（stock/future/crypto）
        query: 用户查询/需求
        strategy_examples: 历史成功策略参考
        strategy_context: 当前策略上下文
        master_hints_context: 大师策略生成建议的可读文本段落，为空串时
            与原行为完全一致（prompt 不出现 master_hints 字样）

    Returns:
        格式化后的提示词字符串
    """
    return _research_prompt_template.format(
        target_market=target_market,
        query=query,
        strategy_examples=strategy_examples,
        strategy_context=strategy_context,
        master_hints_context=master_hints_context,
    )


def render_strategy_optimize_prompt(
    strategy: Any,
    suggestions_text: str,
    backtest_history: str,
    market_characteristics: str,
    operator_catalog: str = "",
) -> str:
    """渲染策略优化提示词

    Args:
        strategy: 当前策略（dict 或 str，自动 str() 转换）
        suggestions_text: 改进建议文本
        backtest_history: 历史回测结果
        market_characteristics: 市场特征
        operator_catalog: 运行时可用算子清单文本（由 _format_operator_catalog 生成）；
            空串时 prompt 中算子目录区域为空白

    Returns:
        格式化后的提示词字符串
    """
    return _optimize_prompt_template.format(
        strategy=strategy,
        suggestions_text=suggestions_text,
        backtest_history=backtest_history,
        market_characteristics=market_characteristics,
        operator_catalog=operator_catalog,
    )
