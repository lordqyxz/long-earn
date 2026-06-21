"""算子目录策略执行器 —— 把算子目录接入策略执行路径。

本模块是"调整系统架构"的关键连接件：策略 DSL 的算子引用步骤（``operator_factors``
与 ``type: operator`` 信号步骤）经此执行器跑在算子目录上，**绕过旧的表达式求值器**
(``SafeExpressionEvaluator``)。

执行语义（因果性由算子目录保证，与 :mod:`visibility` 同源）：
1. 在 polars 历史面板（``timestamp <= 当前时刻``，由 VisibilityGuard 保证）上依次
   跑 factor 算子，把结果列并回面板；
2. 跑 signal 算子（filter_threshold / rank_top）做行选择；
3. 取当前时刻截面 → 选中标的列表。

输入面板的行序任意：算子内部用 ``temporal_series`` 对齐回原始行序。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import polars as pl

from long_earn.backtest.operators import get_operator
from long_earn.backtest.operators.base import Operator, OperatorParams


@dataclass
class OperatorFactorSpec:
    """已校验的算子因子步骤。"""

    op: Operator
    alias: str
    params: OperatorParams


@dataclass
class OperatorSignalSpec:
    """已校验的算子信号步骤（filter / rank）。"""

    op: Operator
    params: OperatorParams


def resolve_factor_step(step: dict[str, Any]) -> OperatorFactorSpec:
    """把 DSL 里的 ``{op, alias, params}`` 解析为已校验的算子因子步骤。

    解析期校验：op 在目录里、params 符合 params_cls。失败抛 ValueError——
    这是"消灭 refine 循环"的关键：参数错误在解析期就被拦下，根本进不到回测。
    """
    alias = step.get("alias", "")
    if not alias:
        raise ValueError(f"算子因子步骤缺少 alias: {step}")
    op, params = _resolve_op(step)
    return OperatorFactorSpec(op=op, alias=alias, params=params)


def resolve_signal_step(step: dict[str, Any]) -> OperatorSignalSpec:
    """把 DSL 里的 ``{type: operator, op, params}`` 解析为已校验的算子信号步骤。"""
    op, params = _resolve_op(step)
    return OperatorSignalSpec(op=op, params=params)


def _resolve_op(step: dict[str, Any]) -> tuple[Operator, OperatorParams]:
    """从步骤 dict 取出算子实例 + 已校验参数（op 存在 + params 合法）。"""
    if "op" not in step:
        raise ValueError(f"算子步骤缺少 op: {step}")
    try:
        op = get_operator(step["op"])
    except KeyError as exc:
        raise ValueError(f"未知算子 '{step['op']}'") from exc
    params_cls = type(op).params_cls
    try:
        params = params_cls.model_validate(step.get("params", {}))
    except Exception as exc:
        raise ValueError(
            f"算子 '{type(op).name}' 参数校验失败 {step.get('params', {})!r}: {exc}"
        ) from exc
    return op, params


class OperatorStrategyExecutor:
    """在 polars 面板上执行算子因子 + 信号步骤，产出当前时刻选中的标的。

    因果性：算子目录每个算子均过因果性证明（见
    :mod:`long_earn.backtest.operators.causality`），且输入面板仅含
    ``timestamp <= 当前时刻`` 的数据（VisibilityGuard 保证），故执行结果无未来函数。
    """

    def __init__(
        self,
        factor_specs: list[OperatorFactorSpec],
        signal_specs: list[OperatorSignalSpec],
    ) -> None:
        self.factor_specs = factor_specs
        self.signal_specs = signal_specs

    def execute(self, panel: pl.DataFrame, current_timestamp: datetime) -> list[str]:
        """执行算子链，返回当前时刻选中的 symbol 列表。"""
        if panel.height == 0:
            return []

        enriched = panel
        # 1) factor 算子：把结果列并回面板
        for spec in self.factor_specs:
            result = spec.op.apply(enriched, spec.params)
            enriched = _merge_result(enriched, result, spec.alias)

        # 2) signal 算子：行选择
        selected_df = enriched
        for spec in self.signal_specs:
            result = spec.op.apply(selected_df, spec.params)
            selected_df = _apply_signal_result(selected_df, result)

        if selected_df.height == 0:
            return []

        # 3) 取当前时刻截面 → 选中标的
        cross = selected_df.filter(pl.col("timestamp") == current_timestamp)
        if cross.height == 0:
            return []
        return cross["symbol"].unique().to_list()


def _merge_result(
    panel: pl.DataFrame, result: pl.Series | pl.DataFrame, alias: str
) -> pl.DataFrame:
    """把算子输出并回面板：Series → 以 alias 为列名追加；DataFrame → 追加全部列。"""
    if isinstance(result, pl.Series):
        return panel.with_columns(result.alias(alias))
    # DataFrame（如 macd/bollinger 多列）：追加其全部列
    cols = {c: result[c] for c in result.columns}
    return panel.with_columns(**cols)


def _apply_signal_result(
    panel: pl.DataFrame, result: pl.Series | pl.DataFrame
) -> pl.DataFrame:
    """应用信号算子输出做行选择。

    - filter 类（bool Series）：保留 True 行；
    - rank 类（带 rank 列的 DataFrame）：保留 rank 非空行。
    """
    if isinstance(result, pl.Series):
        mask = result.fill_null(False)
        return panel.filter(mask)
    if "rank" in result.columns:
        # rank 列已对齐 panel 行序；保留 rank 非空（即入选 top N）
        return panel.filter(result["rank"].is_not_null())
    return panel
