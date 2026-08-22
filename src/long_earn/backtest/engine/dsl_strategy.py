"""DSL 策略适配器

把 YAML DSL 描述的 ``StrategyDSL`` 适配为可运行的 ``BaseStrategy``，
走算子目录执行路径（ADR-009 收尾：仅支持算子目录，旧式 factors /
filter / rank / expression 路径已退役）。

来源：自 ``services/backtest_service.py`` 迁入，以恢复依赖方向
（backtest.engine 属领域层，不得反向依赖 services）。
"""

import re
from typing import Any

import polars as pl

from long_earn.backtest.engine.dsl import StrategyDSL
from long_earn.backtest.engine.strategy import BaseStrategy

# rebalance_freq 合法格式："<N>D"（N 个交易日，如 "20D"）
_REBALANCE_FREQ_RE = re.compile(r"^(\d+)D$")


def parse_rebalance_days(freq: str) -> int:
    """解析 ``rebalance_freq`` 为交易日数。

    非法值（空串/格式错/非正数）退化为 1（每日调仓），并保持向后兼容：
    历史策略未声明或声明非法时行为与修复前一致。
    """
    m = _REBALANCE_FREQ_RE.match(freq.strip())
    if not m:
        return 1
    n = int(m.group(1))
    return n if n >= 1 else 1


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
        self._rebalance_days = parse_rebalance_days(
            dsl_strategy.universe.rebalance_freq
        )
        # 交易日计数（on_bar 调用次数）：0 表示尚未开仓
        self._bar_count = 0
        # 静默吞异常的诊断窗口：上层可读取这两个列表判断策略是否真在工作，
        # 还是只是退化成"什么都不做"而被错误标记 success=True。
        # factor_failures 保留为空列表（旧字段，向后兼容诊断逻辑），算子路径不写入。
        self.factor_failures: list[dict[str, str]] = []
        self.step_failures: list[dict[str, str]] = []

    def init(self) -> None:
        """每 run 重置调仓相位（walk-forward 复用实例时避免跨 fold 相位漂移）。"""
        self._bar_count = 0

    def _should_rebalance(self) -> bool:
        """调仓频率门控：首个交易日建仓，之后每 N 个交易日调仓一次。"""
        if self._rebalance_days <= 1:
            return True
        return self._bar_count % self._rebalance_days == 0

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

        调仓频率门控：非调仓日返回 None（持仓保持），首个交易日建仓，
        之后每 ``rebalance_freq`` 个交易日调仓一次。风控（止损/清仓）在
        引擎层独立运行，不受此门控影响。
        """
        from long_earn.backtest.domain.entities import SignalEvent  # noqa: PLC0415

        do_rebalance = self._should_rebalance()
        self._bar_count += 1
        if not do_rebalance:
            return None

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
