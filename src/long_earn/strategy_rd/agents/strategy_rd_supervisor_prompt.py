"""策略研究监督器提示词模块

提供策略评估和迭代决策的提示词模板。
"""

from long_earn.core.prompt_loader import MarkdownPromptTemplate

# 策略监督器评估提示词 - 从 Markdown 文件加载
strategy_rd_supervisor_prompt = MarkdownPromptTemplate(
    "strategy_rd_supervisor_prompt.md",
    ["strategy", "backtest_result", "decision_history"],
    __file__,
)

# 策略监督器继续迭代决策提示词 - 从 Markdown 文件加载
strategy_rd_supervisor_continue_prompt = MarkdownPromptTemplate(
    "strategy_rd_supervisor_continue_prompt.md",
    [
        "iteration",
        "max_iterations",
        "remaining_iterations",
        "strategy",
        "backtest_result",
        "reflection",
        "improvement_suggestions",
        "decision_history",
        "iteration_history",
    ],
    __file__,
)
