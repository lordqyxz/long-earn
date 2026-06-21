import json
from typing import TYPE_CHECKING, Any

from long_earn.core.llm_utils import parse_llm_json

# Sharpe 阈值：达到此水平视为"业绩明确达标"，停止迭代以节省预算
_GOOD_SHARPE_THRESHOLD = 1.5

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
        self.llm_service = context.require_llm()
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
        """判断是否继续迭代

        多轮演进韧性：
        - LLM JSON 解析失败时不应让节点崩溃；
        - 在 max_iterations 内、回测未明确达标时，**默认继续**而非默认停止
          —— 之前的默认 False 导致 LLM 输出稍微非标准就让系统永远卡在第 1 轮。
        - 仅当 LLM 显式说停或回测明显达标时才停止。
        """
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
        try:
            response = self.llm_service.invoke(prompt, format="json")
            content = response.content.strip()
            result = parse_llm_json(content)
        except (json.JSONDecodeError, ValueError, RuntimeError) as exc:
            if self.logger:
                self.logger.warning(
                    f"监督器 JSON 解析失败({exc})，回退默认：仍在迭代预算内则继续"
                )
            return True

        # 显式 should_continue 优先；缺省时按业绩信号兜底（业绩明确达标→停止；否则继续）
        if "should_continue" in result:
            should_continue = bool(result.get("should_continue"))
        else:
            sharpe = backtest_result.get("sharpe_ratio") or (
                backtest_result.get("metrics", {}) or {}
            ).get("sharpe_ratio") or 0
            already_good = (
                isinstance(sharpe, (int, float)) and sharpe >= _GOOD_SHARPE_THRESHOLD
            )
            should_continue = not already_good

        if self.logger:
            self.logger.info(
                f"监督器决策：{'继续' if should_continue else '停止'}, "
                f"原因：{result.get('reason', '(LLM 未提供 reason)')}"
            )
        return should_continue
