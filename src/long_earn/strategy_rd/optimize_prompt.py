"""策略优化提示词 — CLI optimize 与 OptimizeDelegate 共用。"""

from __future__ import annotations

from typing import Any

from long_earn.core.prompt_loader import MarkdownPromptTemplate

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


def render_strategy_optimize_prompt(
    strategy: Any,
    suggestions_text: str,
    backtest_history: str,
    market_characteristics: str,
    operator_catalog: str = "",
) -> str:
    """渲染策略优化提示词。"""
    return _optimize_prompt_template.format(
        strategy=strategy,
        suggestions_text=suggestions_text,
        backtest_history=backtest_history,
        market_characteristics=market_characteristics,
        operator_catalog=operator_catalog,
    )
