"""策略反思提示词模块

提供策略回测结果分析和改进建议的提示词模板。
"""

from long_earn.core.prompt_loader import MarkdownPromptTemplate

# 策略反思提示词 - 从 Markdown 文件加载
strategy_reflection_prompt = MarkdownPromptTemplate(
    "strategy_reflection_prompt.md",
    ["strategy", "backtest_result", "reflection_history"],
    __file__,
)
