"""DSL 策略适配器

把 YAML DSL 描述的 ``StrategyDSL`` 适配为可运行的 ``BaseStrategy``，
走算子目录执行路径（ADR-009 收尾：仅支持算子目录，旧式 factors /
filter / rank / expression 路径已退役）。

来源：自 ``services/backtest_service.py`` 迁入，以恢复依赖方向
（backtest.engine 属领域层，不得反向依赖 services）。
"""

from typing import Any

import polars as pl

from long_earn.backtest.engine.dsl import StrategyDSL
from long_earn.backtest.engine.strategy import BaseStrategy


class DSLStrategy(BaseStrategy):
    """从 YAML DSL 自动生成的状态化策略（ADR-009 收尾：仅算子目录路径）

    旧式 factors + filter/rank/expression 信号路径已退役，所有策略必须含
    operator_factors 或 type=operator 信号步骤（DSL 解析期强制校验）。
    因果性由算子目录（每个算子过 prove_causality）+ VisibilityGuard
    （history 仅含 timestamp <= 当前时刻）共同保证。
    """

    def __init__(
        self, strategy_id: str, dsl_strategy: StrategyDSL, config: dict | None = None
    ):
        super().__init__(strategy_id, config)
        self.dsl = dsl_strategy
        # 静默吞异常的诊断窗口：上层可读取这两个列表判断策略是否真在工作，
        # 还是只是退化成"什么都不做"而被错误标记 success=True。
        # factor_failures 保留为空列表（旧字段，向后兼容诊断逻辑），算子路径不写入。
        self.factor_failures: list[dict[str, str]] = []
        self.step_failures: list[dict[str, str]] = []

    def _build_operator_executor(self):
        """惰性构造算子目录执行器。

        把算子目录接入策略执行路径：算子因子/信号步骤经此执行器跑在算子目录上。
        解析期已校验过 op/params，这里直接 resolve。
        """
        from long_earn.backtest.engine.operator_executor import (  # noqa: PLC0415
            OperatorStrategyExecutor,
            resolve_factor_step,
            resolve_signal_step,
        )

        factor_specs = [resolve_factor_step(s) for s in self.dsl.operator_factors]
        signal_specs = [
            resolve_signal_step(s)
            for s in self.dsl.signals
            if s.get("type") == "operator"
        ]
        return OperatorStrategyExecutor(factor_specs, signal_specs)

    def on_bar(self, bars: pl.DataFrame, context) -> Any:  # noqa: ARG002
        """算子目录执行路径：在 polars 历史面板上跑算子链 → 选中标的 → 等权信号。

        ADR-009 收尾：旧式 factors + expression 路径已退役，所有策略必须含
        operator_factors 或 operator 信号步骤（DSL 解析期强制校验）。
        ``bars`` 参数为 BaseStrategy.on_bar 契约要求，算子路径改用 history 面板。
        """
        from long_earn.backtest.domain.entities import SignalEvent  # noqa: PLC0415

        if not hasattr(self, "_op_executor"):
            self._op_executor = self._build_operator_executor()

        try:
            history_pl = context.get_history_df()
        except Exception as exc:
            self.step_failures.append(
                {
                    "type": "history_fetch",
                    "step": "on_bar history",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            return None

        try:
            selected, rationale = self._op_executor.execute_with_rationale(
                history_pl, context.current_timestamp
            )
        except Exception as exc:
            self.step_failures.append(
                {
                    "type": "operator_execute",
                    "step": "operator_executor",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            return None

        final_weights = self._equal_weights(selected)
        if not final_weights:
            return None

        return SignalEvent(
            timestamp=context.current_timestamp,
            trace_id=f"op_{context.current_timestamp.isoformat()}",
            event_id=f"op_{context.current_timestamp.isoformat()}",
            signals=final_weights,
            strategy_id=self.strategy_id,
            metadata={"rationale": self._rationale_with_weights(rationale)},
        )

    def _rationale_with_weights(self, rationale: dict[str, Any]) -> dict[str, Any]:
        """给执行器的决策依据补上权重口径与人类可读公式（供审计归因展示）。"""
        weights = getattr(self.dsl, "weights", None)
        method = getattr(weights, "method", "") if weights is not None else ""
        criteria = rationale.get("criteria", [])
        formula = "；".join(c.get("desc", "") for c in criteria)
        if method == "equal":
            formula = f"{formula}；等权"
        rationale["formula"] = formula
        rationale["weights"] = {"method": method} if method else {}
        return rationale

    def _equal_weights(self, selected: list) -> dict[str, float]:
        if not selected:
            self.step_failures.append(
                {
                    "type": "weights",
                    "step": "method=equal",
                    "error": "selected 为空：信号步骤未选出任何标的",
                }
            )
            return {}
        weight = 1.0 / len(selected)
        return dict.fromkeys(selected, weight)
