import json

from typing import Any, Dict

from long_earn.utils.logger import LOGGER


class StrategyRdSupervisor:
    """策略研究监督器"""

    def __init__(
        self, llm_type: str = "ollama", model_name: str = "", base_url: str = ""
    ):
        self.llm_type = llm_type
        self.model_name = model_name
        self.base_url = base_url

    def _create_llm(self):
        from long_earn.utils.llm_factory import create_llm

        return create_llm(
            llm_type=self.llm_type or "ollama",
            model_name=self.model_name or "qwen3.5:9b",
            base_url=self.base_url or "http://localhost:11434"
        )

    def evaluate_strategy(
        self, strategy: Dict[str, Any], backtest_result: Dict[str, Any]
    ) -> bool:
        """评估策略 - 判断是否接受优化建议"""
        from .strategy_rd_supervisor_prompt import strategy_rd_supervisor_prompt

        llm = self._create_llm()

        prompt = strategy_rd_supervisor_prompt.format(
            strategy=strategy,
            backtest_result=backtest_result,
            decision_history="无",
        )
        response = llm.invoke(prompt)

        content = response.content.strip()
        result = json.loads(content)
        decision = result.get("decision", "拒绝")
        LOGGER.info(f"监督器评估结果: {decision}, 原因: {result.get('reason', '')}")
        return decision == "接受"

    def should_continue(
        self,
        iteration: int,
        max_iterations: int,
        strategy: Dict[str, Any],
        backtest_result: Dict[str, Any],
        reflection: str,
        improvement_suggestions: str,
    ) -> bool:
        """判断是否继续迭代"""
        from .strategy_rd_supervisor_prompt import (
            strategy_rd_supervisor_continue_prompt,
        )

        if iteration >= max_iterations:
            LOGGER.info(f"已达到最大迭代次数 {max_iterations}，停止迭代")
            return False

        llm = self._create_llm()

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
        response = llm.invoke(prompt)

        content = response.content.strip()
        result = json.loads(content)
        should_continue = result.get("should_continue", False)
        LOGGER.info(
            f"监督器决策: {'继续' if should_continue else '停止'}, 原因: {result.get('reason', '')}"
        )

        return should_continue
