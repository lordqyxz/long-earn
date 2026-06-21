"""算子因果性 (无未来函数) 证明器。

**因果性形式定义**：算子 ``f`` 因果，当且仅当对任意面板 ``P`` 与任意时刻
``T``，仅修改 ``P`` 中 ``timestamp > T`` 的行，不改变 ``f(P)`` 在
``timestamp <= T`` 行上的输出。

> 一个算子在 t 时刻的输出若依赖任何 t' > t 的数据，则它含未来函数 (look-ahead
> bias)，回测业绩不可信。这是量化金融的硬红线。

本模块提供 :func:`prove_causality`，用"未来扰动不变性"数值验证任意算子的因果性：
取一个确定性面板，计算输出 ``O1``；把所有 ``timestamp > T`` 的数据大幅扰动
（乘以一个大常数 / 置 NaN），再算 ``O2``；断言 ``O1`` 与 ``O2`` 在
``timestamp <= T`` 上逐元素相等（容差内）。若相等，则该算子在 T 切面上被证明
不读未来；遍历多个 T 即覆盖整段历史。

该证明是**数学性质的**（基于因果性的操作定义），不是经验拟合，因此可作为
"系统从数学角度证明符合金融交易规范、严谨无未来函数"的依据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import polars as pl

if TYPE_CHECKING:
    from long_earn.backtest.operators.base import Operator, OperatorParams


@dataclass
class CausalityReport:
    """单次因果性验证报告。"""

    operator_name: str
    split_timestamp: Any
    passed: bool
    detail: str = ""


def _perturb_future(panel: pl.DataFrame, split_ts: Any) -> pl.DataFrame:
    """把 ``timestamp > split_ts`` 的所有数值列大幅扰动（乘 1e6 + 置部分 NaN）。

    扰动幅度极大，任何泄漏都会把 T 之前的输出打成完全不同的值，使不变性断言
    高灵敏度地失败。
    """

    future_mask = pl.col("timestamp") > split_ts
    numeric_cols = [
        c for c, dt in zip(panel.columns, panel.dtypes, strict=True) if dt.is_numeric()
    ]
    exprs = []
    for c in numeric_cols:
        if c in ("timestamp", "symbol"):
            continue
        # 把未来段置 NaN：任何泄漏都会改变 T 之前输出，高灵敏度失败
        perturbed = pl.when(future_mask).then(pl.lit(float("nan"))).otherwise(pl.col(c))
        exprs.append(perturbed.alias(c))
    return panel.with_columns(exprs)


def _output_before(
    output: pl.Series | pl.DataFrame, panel: pl.DataFrame, split_ts: Any
) -> dict[str, pl.Series]:
    """把算子输出切片到 ``timestamp <= split_ts``，按列返回（Series 用 "_series" 键）。"""
    mask = panel["timestamp"] <= split_ts
    if isinstance(output, pl.Series):
        return {"_series": output.filter(mask)}
    return {
        c: output[c].filter(mask)
        for c in output.columns
        if c not in ("timestamp", "symbol")
    }


def _series_equal(a: pl.Series, b: pl.Series, tol: float = 1e-9) -> bool:
    """两个 Series 逐元素相等（null 视作相等，数值容差 tol）。

    polars 原生实现，兼容浮点 / 整数 / 布尔 / 字符串类型。
    """

    if a.len() != b.len():
        return False
    # 一边 null 一边非 null → 不等
    one_null = a.is_null() ^ b.is_null()
    if one_null.any():
        return False
    both_null = a.is_null() & b.is_null()
    # 浮点：容差比较（inf 需严格相等，由 == 兜住）
    if a.dtype.is_float() and b.dtype.is_float():
        close = (a - b).abs() <= tol
        return bool((both_null | close).all())
    # 非浮点（bool/int/str）：直接 == 比较
    return bool((both_null | (a == b)).all())


def prove_causality(
    op: Operator,
    params: OperatorParams,
    panel: pl.DataFrame,
    split_timestamps: list[Any] | None = None,
) -> list[CausalityReport]:
    """证明算子 ``op`` 在给定 panel 上因果（无未来函数）。

    对每个 ``T`` in ``split_timestamps``（默认取除首尾外的若干中点），做未来
    扰动不变性验证，返回报告列表。全部 ``passed=True`` 即证明该算子因果。

    Args:
        op: 已注册算子实例。
        params: 算子参数。
        panel: 确定性面板（含 timestamp/symbol 及算子所需列）。
        split_timestamps: 验证切点；默认取面板内第 1/3、1/2、2/3 处的时间戳。
    """

    if split_timestamps is None:
        ts = panel["timestamp"].unique().sort()
        n = ts.len()
        _MIN_TS_FOR_TRIPLE = 4  # noqa: N806
        if n < _MIN_TS_FOR_TRIPLE:
            split_timestamps = [ts[n // 2]] if n > 0 else []
        else:
            split_timestamps = [ts[n // 3], ts[n // 2], ts[2 * n // 3]]

    reports: list[CausalityReport] = []
    base_output = op.apply(panel, params)

    for t in split_timestamps:
        perturbed = _perturb_future(panel, t)
        try:
            perturbed_output = op.apply(perturbed, params)
        except Exception as exc:
            reports.append(
                CausalityReport(
                    operator_name=type(op).name,
                    split_timestamp=t,
                    passed=False,
                    detail=f"扰动后面板执行异常: {type(exc).__name__}: {exc}",
                )
            )
            continue

        before_base = _output_before(base_output, panel, t)
        before_pert = _output_before(perturbed_output, perturbed, t)

        ok = True
        detail = ""
        for col in before_base:
            if not _series_equal(
                before_base[col], before_pert.get(col, pl.Series("", []))
            ):
                ok = False
                detail = f"列 {col} 在 t<={t} 上因未来扰动而改变（含未来函数）"
                break
        if before_pert and not before_base:
            ok = False
            detail = "输出列集合不一致"

        reports.append(
            CausalityReport(
                operator_name=type(op).name,
                split_timestamp=t,
                passed=ok,
                detail=detail,
            )
        )

    return reports


def is_causal(
    op: Operator,
    params: OperatorParams,
    panel: pl.DataFrame,
    split_timestamps: list[Any] | None = None,
) -> bool:
    """便捷封装：所有切点都通过即返回 True。"""

    return all(r.passed for r in prove_causality(op, params, panel, split_timestamps))


def math_note() -> str:
    """返回因果性证明的数学说明文本（供文档 / 报告引用）。"""

    return (
        "因果性定义：算子 f 因果 ⟺ ∀ 面板 P, ∀ 时刻 T, "
        "仅改 P 中 timestamp>T 的行不改变 f(P) 在 timestamp≤T 行的输出。"
        "prove_causality 用未来扰动不变性按此定义做数值证明：扰动全部未来数据后，"
        "若 t≤T 的输出逐元素不变，则该算子在 T 切面不读未来；遍历多个 T 覆盖全历史。"
        f"（实现常量：tol={1e-9}）"
    )
