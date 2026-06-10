from typing import TYPE_CHECKING, Any

from long_earn.core.llm_utils import parse_llm_json

from .strategy_rd_supervisor_prompt import (
    strategy_rd_supervisor_continue_prompt,
    strategy_rd_supervisor_prompt,
)

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext


class StrategyRdSupervisor:
    """策略研究监督器"""

    def __init__(self, context: "RuntimeContext"):
        """初始化监督器

        Args:
            context: 运行时上下文
        """
        self.context = context
        self.llm_service = context.llm_service
        self.logger = context.logger

    def evaluate_strategy(
        self, strategy: dict[str, Any], backtest_result: dict[str, Any]
    ) -> bool:
        """评估策略 - 判断是否接受优化建议"""

        prompt = strategy_rd_supervisor_prompt.format(
            strategy=strategy,
            backtest_result=backtest_result,
            decision_history="无",
        )
        response = self.llm_service.invoke(prompt, format="json")

        content = response.content.strip()
        result = parse_llm_json(content)
        decision = result.get("decision", "接受")
        if self.logger:
            self.logger.info(
                f"监督器评估结果：{decision}, 原因：{result.get('reason', '')}"
            )
        return decision == "接受"

    def should_continue(  # noqa: PLR0913
        self,
        iteration: int,
        max_iterations: int,
        strategy: dict[str, Any],
        backtest_result: dict[str, Any],
        reflection: str,
        improvement_suggestions: str,
    ) -> bool:
        """判断是否继续迭代"""

        if iteration >= max_iterations:
            if self.logger:
                self.logger.info(f"已达到最大迭代次数 {max_iterations}，停止迭代")
            return False

        remaining_iterations = max_iterations - iteration
        prompt = strategy_rd_supervisor_continue_prompt.format(
            iteration=iteration,
            max_iterations=max_iterations,
            remaining_iterations=remaining_iterations,
            strategy=strategy,
            backtest_result=backtest_result,
            reflection=reflection,
            improvement_suggestions=improvement_suggestions,
            decision_history="无",
            iteration_history="无",
        )
        response = self.llm_service.invoke(prompt, format="json")

        content = response.content.strip()
        result = parse_llm_json(content)
        should_continue = result.get("should_continue", False)
        if self.logger:
            self.logger.info(
                f"监督器决策：{'继续' if should_continue else '停止'}, 原因：{result.get('reason', '')}"
            )

        return should_continue
