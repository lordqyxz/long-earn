"""算子目录策略执行器 —— 策略 DSL 唯一执行路径（ADR-009 收尾）。

本模块是策略 DSL 的算子引用步骤（``operator_factors`` 与 ``type: operator``
信号步骤）的唯一执行器。ADR-009 收尾后旧的表达式求值器
（``SafeExpressionEvaluator``）已退役，所有策略必须走算子目录。

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
from loguru import logger

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
        # ADR-015 C2: 空信号 WARNING 日志采样计数器，避免长回测日志爆炸
        self._empty_signal_count = 0
        self._empty_cross_count = 0

    # ADR-015 C2: 每 N 次空信号才打印一次 WARNING，防止日志爆炸
    _EMPTY_SIGNAL_LOG_INTERVAL = 100
    _EMPTY_CROSS_LOG_INTERVAL = 100

    def execute(self, panel: pl.DataFrame, current_timestamp: datetime) -> list[str]:
        """执行算子链，返回当前时刻选中的 symbol 列表（兼容旧接口）。"""
        selected, _ = self.execute_with_rationale(panel, current_timestamp)
        return selected

    def execute_with_rationale(
        self, panel: pl.DataFrame, current_timestamp: datetime
    ) -> tuple[list[str], dict[str, Any]]:
        """执行算子链，返回 ``(选中标的列表, 决策依据 rationale)``。

        rationale（供审计归因展示）含：
        - criteria: 算子步骤描述（因子公式 + 信号步骤），含 ``format`` 提示
          （如 returns 类标记为百分比）
        - selection: 每只选中标的的因子值 + 排名（rank）
        - universe_size / selected_count: 信号过滤前截面候选数 / 最终选中数

        因果性：输入面板仅含 ``timestamp <= 当前时刻`` 的数据（VisibilityGuard
        保证），算子目录每算子均过因果性证明，执行结果无未来函数。
        """
        if panel.height == 0:
            logger.debug("OperatorStrategyExecutor: 输入面板为空，无选中标的")
            return [], self._rationale([], 0, 0)

        enriched = panel
        factor_columns: list[str] = []
        # 1) factor 算子：把结果列并回面板（记录新增因子列名供归因取值）
        for spec in self.factor_specs:
            result = spec.op.apply(enriched, spec.params)
            enriched, added = _merge_factor_result(enriched, result, spec.alias)
            factor_columns.extend(added)

        # 信号过滤前的当前时刻截面（universe 口径）
        universe_cross = enriched.filter(pl.col("timestamp") == current_timestamp)
        universe_size = universe_cross["symbol"].unique().len()

        # 2) signal 算子：行选择
        selected_df = enriched
        for spec in self.signal_specs:
            result = spec.op.apply(selected_df, spec.params)
            selected_df = _apply_signal_result(selected_df, result)

        if selected_df.height == 0:
            self._empty_signal_count += 1
            if self._empty_signal_count == 1 or (
                self._empty_signal_count % self._EMPTY_SIGNAL_LOG_INTERVAL == 0
            ):
                logger.warning(
                    "OperatorStrategyExecutor: signal 算子过滤后 selected_df 为空"
                    f"（timestamp={current_timestamp}），累计 {self._empty_signal_count} 次"
                )
            return [], self._rationale([], universe_size, 0)

        # 3) 取当前时刻截面 → 选中标的
        cross = selected_df.filter(pl.col("timestamp") == current_timestamp)
        if cross.height == 0:
            self._empty_cross_count += 1
            if self._empty_cross_count == 1 or (
                self._empty_cross_count % self._EMPTY_CROSS_LOG_INTERVAL == 0
            ):
                logger.warning(
                    "OperatorStrategyExecutor: 当前时刻截面无选中标的"
                    f"（timestamp={current_timestamp}），累计 {self._empty_cross_count} 次"
                )
            return [], self._rationale([], universe_size, 0)
        symbols = cross["symbol"].unique().to_list()
        selection = self._build_selection(cross, factor_columns)
        # 按排名升序（#1 在前），让归因展示与排名一致；symbols 跟随同一顺序
        selection.sort(
            key=lambda s: s.get("rank")
            if isinstance(s.get("rank"), int)
            else float("inf")
        )
        symbols = [s["symbol"] for s in selection]
        return symbols, self._rationale(selection, universe_size, len(symbols))

    def _rationale(
        self,
        selection: list[dict[str, Any]],
        universe_size: int,
        selected_count: int,
    ) -> dict[str, Any]:
        """组装决策依据字典。"""
        return {
            "criteria": self._criteria(),
            "selection": selection,
            "universe_size": int(universe_size),
            "selected_count": int(selected_count),
        }

    def _criteria(self) -> list[dict[str, Any]]:
        """生成算子步骤的人类可读描述列表（因子公式 + 信号步骤）。"""
        steps: list[dict[str, Any]] = []
        for spec in self.factor_specs:
            steps.append(_describe_step(spec, is_factor=True))
        for spec in self.signal_specs:
            steps.append(_describe_step(spec, is_factor=False))
        return steps

    def _build_selection(
        self, cross: pl.DataFrame, factor_columns: list[str]
    ) -> list[dict[str, Any]]:
        """把当前时刻截面抽成 ``{symbol, rank, <因子>: 值}`` 列表（供归因展示）。"""
        selection: list[dict[str, Any]] = []
        for row in cross.iter_rows(named=True):
            item: dict[str, Any] = {"symbol": row.get("symbol")}
            rank = row.get("rank")
            if rank is not None:
                item["rank"] = int(rank)
            for col in factor_columns:
                val = row.get(col)
                if val is not None:
                    item[col] = float(val) if isinstance(val, (int, float)) else val
            selection.append(item)
        return selection


def _merge_factor_result(
    panel: pl.DataFrame, result: pl.Series | pl.DataFrame, alias: str
) -> tuple[pl.DataFrame, list[str]]:
    """把算子输出并回面板，返回 (新面板, 新增列名列表)（新增列名供归因取值）。"""
    if isinstance(result, pl.Series):
        return panel.with_columns(result.alias(alias)), [alias]
    # DataFrame（如 macd/bollinger 多列）：追加其全部列
    cols = {c: result[c] for c in result.columns}
    return panel.with_columns(**cols), list(result.columns)


def _describe_step(spec: Any, is_factor: bool) -> dict[str, Any]:
    """生成单步算子的人类可读描述（公式片段）+ 数值格式提示。

    常见算子走模板给出干净中文描述；未知算子回退到 docstring 首行。
    ``format`` 字段供前端决定数值渲染（pct=百分比，其余按原值）。
    """
    op_name = type(spec.op).name
    p = spec.params
    alias = spec.alias if is_factor else ""
    params = p.model_dump()
    fmt = ""
    if op_name == "returns":
        period = getattr(p, "period", 1)
        field = getattr(p, "field", "close")
        head = f"{alias} = " if alias else ""
        desc = f"{head}{field} 的 {period} 期收益率"
        fmt = "pct"
    elif op_name == "filter_threshold":
        desc = (
            f"筛选 {getattr(p, 'field', '')} {getattr(p, 'op', '>')} "
            f"{getattr(p, 'value', 0)}"
        )
    elif op_name == "rank_top":
        order = "降序" if not getattr(p, "ascending", False) else "升序"
        desc = f"按 {getattr(p, 'field', '')} {order} 取前 {getattr(p, 'top', 10)}"
    else:
        doc = (type(spec.op).__doc__ or "").strip().split("\n")[0]
        desc = doc.strip("` ") or op_name
    return {
        "step": "factor" if is_factor else "signal",
        "op": op_name,
        "alias": alias,
        "params": params,
        "desc": desc,
        "format": fmt,
    }


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
        # rank 列在 result 上（与 panel 行序对齐），过滤 panel 后把 rank 值并回，
        # 供归因展示每只选中标的的排名
        mask = result["rank"].is_not_null()
        filtered = panel.filter(mask)
        rank_values = result["rank"].filter(mask)
        return filtered.with_columns(rank_values.alias("rank"))
    return panel
