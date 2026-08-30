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


def _selection_rank_key(s: dict[str, Any]) -> float:
    """selection 排序键：rank 为 int 时按其值，None（未入选）排最后。"""
    rank = s.get("rank")
    return float(rank) if isinstance(rank, int) else float("inf")


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
    """把 DSL 里的 ``{type: operator, op, params}`` 解析为已校验的算子信号步骤。

    额外校验截面性（cross_sectional）：预计算执行路径的 signal 算子跑在
    单日截面上，非截面算子（依赖历史的 shift/sma 等）会静默产出 null/
    错值——解析期直接拒绝。prove_causality 只证明不窥未来，不证明不跨
    时刻，两者正交（见 base.Operator.cross_sectional）。
    """
    op, params = _resolve_op(step)
    if not type(op).cross_sectional:
        raise ValueError(
            f"信号算子 {type(op).name!r} 非截面运算（cross_sectional=False），"
            "预计算路径仅支持截面信号算子（如 rank_top / filter_threshold）"
        )
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


def precompute_factors(
    factor_specs: list[OperatorFactorSpec], panel: pl.DataFrame
) -> tuple[pl.DataFrame, list[str]]:
    """全期一次性预计算 factor 链，返回 (enriched, 因子列名列表)。

    正确性依据：算子目录因果性证明（ADR-009 prove_causality）保证
    行 t 的因子值只依赖同 symbol 的 ≤t 行，故全期计算与逐 bar 截断
    计算逐值相等（含 ewm：两侧均从面板首行起算）。等价性由
    ``tests/unit/test_backtest/test_operators/test_precompute_equivalence.py``
    运行时验证——结果发散 = 证明被违反 = bug。
    VisibilityGuard 仍按 timestamp 截断行可见性，双重防线不变。
    """
    enriched = panel
    factor_columns: list[str] = []
    for spec in factor_specs:
        result = spec.op.apply(enriched, spec.params)
        enriched, added = _merge_factor_result(enriched, result, spec.alias)
        factor_columns.extend(added)
    return enriched, factor_columns


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
          （如 returns 类标记为百分比）与结构化渲染数据 ``kind`` / ``segments``
          （见 :func:`_describe_step`，前端按此动态渲染，零算子知识）
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
        return self._finalize_selection(cross, factor_columns, universe_size)

    def execute_precomputed(
        self,
        cross: pl.DataFrame,
        factor_columns: list[str],
        current_timestamp: datetime,
    ) -> tuple[list[str], dict[str, Any]]:
        """在已含因子列的当前截面上一键跑 signal 算子（预计算模式）。

        signal 算子均为截面内运算（rank_top 以 over("timestamp") 保证，
        filter_threshold 为行级比较），故只需当前截面行。与旧路径
        （全历史面板跑 factor+signal 全链）的等价性由因果性证明背书
        （见 precompute_factors docstring）。
        """
        if cross.height == 0:
            self._empty_signal_count += 1
            if self._empty_signal_count == 1 or (
                self._empty_signal_count % self._EMPTY_SIGNAL_LOG_INTERVAL == 0
            ):
                logger.warning(
                    "OperatorStrategyExecutor: 预计算截面为空"
                    f"（timestamp={current_timestamp}），"
                    f"累计 {self._empty_signal_count} 次"
                )
            return [], self._rationale([], 0, 0)

        universe_size = cross["symbol"].unique().len()
        selected_df = cross
        for spec in self.signal_specs:
            result = spec.op.apply(selected_df, spec.params)
            selected_df = _apply_signal_result(selected_df, result)

        if selected_df.height == 0:
            self._empty_signal_count += 1
            if self._empty_signal_count == 1 or (
                self._empty_signal_count % self._EMPTY_SIGNAL_LOG_INTERVAL == 0
            ):
                logger.warning(
                    "OperatorStrategyExecutor: signal 算子过滤后为空"
                    f"（timestamp={current_timestamp}），"
                    f"累计 {self._empty_signal_count} 次"
                )
            return [], self._rationale([], universe_size, 0)

        return self._finalize_selection(selected_df, factor_columns, universe_size)

    def _finalize_selection(
        self,
        cross: pl.DataFrame,
        factor_columns: list[str],
        universe_size: int,
    ) -> tuple[list[str], dict[str, Any]]:
        """从 signal 过滤后的当前截面组装 (选中标的, rationale)。

        旧路径（execute_with_rationale）与预计算路径（execute_precomputed）
        的公共尾部：构建 selection、按排名排序、组装 rationale。
        """
        symbols = cross["symbol"].unique().to_list()
        selection = self._build_selection(cross, factor_columns)
        # 按排名升序（#1 在前），让归因展示与排名一致；symbols 跟随同一顺序
        # rank 为 None（未入选）时排最后；key 恒返回 float，满足排序契约
        selection.sort(key=_selection_rank_key)
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

    added: list[str] = []
    exprs: dict[str, pl.Series] = {}
    for col in result.columns:
        namespaced = f"{alias}_{col}" if alias else col
        if namespaced not in panel.columns and namespaced not in exprs:
            exprs[namespaced] = result[col]
            added.append(namespaced)
        # 向后兼容：首个多列算子仍暴露原始列名（如 macd/signal/histogram），
        # 后续同名步骤只写入 alias 前缀列，避免两步 macd 静默互盖。
        if alias and col not in panel.columns and col not in exprs:
            exprs[col] = result[col]
            added.append(col)
    if not exprs:
        return panel, added
    return panel.with_columns(**exprs), added


def _describe_step(spec: Any, is_factor: bool) -> dict[str, Any]:
    """生成单步算子的人类可读描述 + 结构化渲染数据。

    设计：**渲染知识全部收口在后端**，前端只做数据驱动的通用渲染。
    每个步骤除 ``desc``（纯文本，tooltip/兼容旧数据用）外，还下发：
    - ``kind``：粗粒度步骤类型（factor/filter/rank/generic）→ 前端选图标；
    - ``segments``：有序渲染段（field/value/symbol/text），field 段渲染为
      因子彩色标签、value/symbol 渲染为等宽文本。
    常见算子走模板；**未知算子走通用模板**（按 ``field_params`` 标注把列名
    参数变成 field 段），因此新增算子/改字段名都无需同步前端。
    ``format`` 字段供前端决定数值渲染（pct=百分比，其余按原值）。
    """
    op_name = type(spec.op).name
    p = spec.params
    alias = spec.alias if is_factor else ""
    params = p.model_dump()
    fmt = ""
    kind = _kind_for(type(spec.op).category)
    if op_name in ("returns", "log_return"):
        period = getattr(p, "period", 1)
        field = getattr(p, "field", "close")
        head = f"{alias} = " if alias else ""
        desc = f"{head}{field} 的 {period} 期收益率"
        fmt = "pct" if op_name == "returns" else ""
        segments = [
            _seg_field(field),
            _seg_text(" 的 "),
            _seg_value(period),
            _seg_text(" 期收益率"),
        ]
    elif op_name == "filter_threshold":
        field = getattr(p, "field", "")
        cmp_op = getattr(p, "op", ">")
        value = getattr(p, "value", 0)
        desc = f"筛选 {field} {cmp_op} {value}"
        segments = [
            _seg_text("筛选 "),
            _seg_field(field),
            _seg_symbol(cmp_op),
            _seg_value(value),
        ]
    elif op_name == "rank_top":
        field = getattr(p, "field", "")
        order = "降序" if not getattr(p, "ascending", False) else "升序"
        top = getattr(p, "top", 10)
        desc = f"按 {field} {order} 取前 {top}"
        segments = [
            _seg_text("按 "),
            _seg_field(field),
            _seg_text(f" {order}取前 "),
            _seg_value(top),
        ]
    else:
        doc = (type(spec.op).__doc__ or "").strip().split("\n")[0]
        desc = doc.strip("` ") or op_name
        segments = _generic_segments(op_name, params, type(spec.op).field_params)
    return {
        "step": "factor" if is_factor else "signal",
        "op": op_name,
        "alias": alias,
        "params": params,
        "desc": desc,
        "format": fmt,
        "kind": kind,
        "segments": segments,
    }


def _kind_for(category: str) -> str:
    """算子类别 → 前端图标归属的粗粒度步骤类型。

    filter→filter、rank→rank；factor/technical/compose 均归 factor（公式样式）。
    这是稳定的小型分类法，新增算子复用既有 kind，无需前端改动。
    """
    if category == "filter":
        return "filter"
    if category == "rank":
        return "rank"
    return "factor"


def _seg_text(value: object) -> dict[str, object]:
    """纯文本渲染段。"""
    return {"type": "text", "value": value}


def _seg_field(value: object) -> dict[str, object]:
    """字段（列名）渲染段：前端渲染为因子彩色标签。"""
    return {"type": "field", "value": value}


def _seg_value(value: object) -> dict[str, object]:
    """标量值渲染段：前端渲染为等宽文本。"""
    return {"type": "value", "value": value}


def _seg_symbol(value: object) -> dict[str, object]:
    """比较/运算符符号渲染段：前端渲染为等宽文本。"""
    return {"type": "symbol", "value": value}


def _generic_segments(
    op_name: str, params: dict[str, Any], field_params: list[str]
) -> list[dict[str, object]]:
    """未知算子的通用渲染段：``op(param, param, ...)``。

    ``field_params`` 标注的列名参数渲染为 field 段（因子彩色标签），其余参数
    渲染为 value 段。因此新增算子即使没有专属模板，字段名也能正确高亮，
    且改字段名只需后端一处改动。
    """
    segments: list[dict[str, object]] = [_seg_text(f"{op_name}(")]
    for index, (key, value) in enumerate(params.items()):
        if index:
            segments.append(_seg_text(", "))
        if key in field_params and isinstance(value, str):
            segments.append(_seg_field(value))
        else:
            segments.append(_seg_value(value))
    segments.append(_seg_text(")"))
    return segments


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
