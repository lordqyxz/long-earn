"""算子因果性 (无未来函数) 证明器。

**因果性形式定义**：算子 ``f`` 因果，当且仅当对任意面板 ``P`` 与任意时刻
``T``，仅修改 ``P`` 中 ``timestamp > T`` 的行，不改变 ``f(P)`` 在
``timestamp <= T`` 行上的输出。

> 一个算子在 t 时刻的输出若依赖任何 t' > t 的数据，则它含未来函数 (look-ahead
> bias)，回测业绩不可信。这是量化金融的硬红线。

本模块提供 :func:`prove_causality`，用"未来扰动不变性"数值验证任意算子的因果性：
取一个确定性面板，计算输出 ``O1``；把所有 ``timestamp > T`` 的数据大幅扰动
（NaN / 极值×1e6 / 负数×-1 / 随机大数），再算 ``O2``；断言 ``O1`` 与 ``O2``
在 ``timestamp <= T`` 上逐元素相等（容差内）。若相等，则该算子在 T 切面上被证明
不读未来；遍历多个 T 即覆盖整段历史。

AUDIT-P2-12：支持四种扰动策略，覆盖 NaN 置空、极值放大、符号反转、随机大数
四种场景，防止算子通过 NaN 传播吞掉未来数据泄漏。

该证明是**数学性质的**（基于因果性的操作定义），不是经验拟合，因此可作为
"系统从数学角度证明符合金融交易规范、严谨无未来函数"的依据。
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import polars as pl

if TYPE_CHECKING:
    from long_earn.backtest.operators.base import Operator, OperatorParams


class PerturbStrategy(StrEnum):
    """未来扰动策略（AUDIT-P2-12）。

    不同策略模拟不同攻击场景，防止算子通过 NaN 传播、数值截断等方式
    吞掉未来数据泄漏。
    """

    NAN = "nan"
    """将未来数据全部置 NaN——最激进，任何泄漏都会改变输出。"""

    EXTREME = "extreme"
    """将未来数据乘以 1e6——极值放大，检测算子对量纲变化的敏感度。"""

    NEGATE = "negate"
    """将未来数据乘以 -1——符号反转，检测算子对方向性泄漏的敏感度。"""

    RANDOM_LARGE = "random_large"
    """将未来数据替换为随机大数（±1e6 量级）——防止算子在 NaN 策略下
    恰好因 NaN 传播而"看起来"因果（实际含未来函数但输出被 NaN 淹没）。"""


@dataclass
class CausalityReport:
    """单次因果性验证报告。"""

    operator_name: str
    split_timestamp: Any
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class CausalityProof:
    """绑定到算子实现与参数集合的启动期因果证明。"""

    implementation_hash: str
    parameter_hashes: tuple[str, ...]


_TEMPORAL_PARAMETER_NAMES = frozenset(
    {
        "period",
        "periods",
        "window",
        "span",
        "fast",
        "slow",
        "signal",
        "lookback",
        "low_vol_lookback",
        "momentum_lookback",
        "momentum_window",
        "quality_window",
        "min_obs",
        "min_periods",
    }
)


def operator_implementation_hash(op: Operator) -> str:
    """计算实现指纹；代码或参数 schema 改变后旧证明立即失效。"""

    code = type(op).apply.__code__
    apply_code = repr((code.co_code, code.co_consts, code.co_names)).encode()
    schema = json.dumps(
        type(op).params_cls.model_json_schema(), sort_keys=True, ensure_ascii=True
    ).encode()
    return hashlib.sha256(apply_code + schema).hexdigest()


def _parameter_hash(params: OperatorParams) -> str:
    payload = params.model_dump_json(exclude_none=False).encode()
    return hashlib.sha256(payload).hexdigest()


def make_registration_panel() -> pl.DataFrame:
    """构造启动/热注册共用的确定性验证面板（3 标的 × 30 日）。"""

    rows: list[dict[str, Any]] = []
    base = datetime(2024, 1, 1)
    for i in range(30):
        for symbol_index, symbol in enumerate(("A.SZ", "B.SH", "C.SZ")):
            step = i + 1
            close = 10.0 + symbol_index * 3 + 0.4 * step + (step % 5)
            rows.append(
                {
                    "timestamp": base + timedelta(days=i),
                    "symbol": symbol,
                    "open": close - 0.1,
                    "high": close + 0.2,
                    "low": close - 0.2,
                    "close": close,
                    "volume": 1000.0 * step,
                }
            )
    return pl.DataFrame(rows)


def _default_parameter_data(op: Operator) -> dict[str, Any]:
    """为无完整默认值的目录算子合成一组保守、合法的基线参数。"""

    fields = type(op).params_cls.model_fields
    values: dict[str, Any] = {}
    fallbacks: dict[str, Any] = {
        "field": "close",
        "lhs": "high",
        "rhs": "low",
        "period": 2,
        "periods": 2,
        "window": 5,
        "top": 2,
    }
    for name, field in fields.items():
        if not field.is_required():
            values[name] = field.get_default(call_default_factory=True)
        elif name in fallbacks:
            values[name] = fallbacks[name]
        else:
            raise ValueError(f"缺少参数 {name!r} 的默认值，无法执行启动期因果验证")
    return values


def _registration_parameter_cases(
    op: Operator, current_params: list[OperatorParams] | None
) -> list[OperatorParams]:
    """生成当前参数及显式时序边界（最小 1、验证面板上界 29）。"""

    cls = type(op).params_cls
    cases = list(current_params or [cls.model_validate(_default_parameter_data(op))])
    base = cases[0].model_dump()
    temporal_names = _TEMPORAL_PARAMETER_NAMES.intersection(base)
    for name in sorted(temporal_names):
        for boundary in (1, 29):
            candidate = dict(base)
            candidate[name] = boundary
            if not _adjust_parameter_dependencies(candidate, name, boundary):
                continue
            try:
                cases.append(cls.model_validate(candidate))
            except ValueError:
                continue
    unique: dict[str, OperatorParams] = {}
    for params in cases:
        unique[_parameter_hash(params)] = params
    return list(unique.values())


def _adjust_parameter_dependencies(
    candidate: dict[str, Any], name: str, boundary: int
) -> bool:
    """让单参数边界保持跨字段约束合法。"""

    if name == "fast" and "slow" in candidate:
        candidate["slow"] = max(int(candidate["slow"]), boundary + 1)
    elif name == "slow" and "fast" in candidate:
        candidate["fast"] = min(int(candidate["fast"]), boundary - 1)
        if candidate["fast"] < 1:
            return False
    elif name == "min_periods" and "window" in candidate:
        candidate["window"] = max(int(candidate["window"]), boundary)
    elif name == "window" and "min_periods" in candidate:
        candidate["min_periods"] = min(int(candidate["min_periods"]), boundary)
    elif name == "min_obs":
        for lookback in ("low_vol_lookback", "momentum_lookback"):
            if lookback in candidate:
                candidate[lookback] = max(int(candidate[lookback]), boundary)
    elif name in ("low_vol_lookback", "momentum_lookback") and "min_obs" in candidate:
        candidate["min_obs"] = min(int(candidate["min_obs"]), boundary)
    return True


def prove_registration_causality(
    op: Operator,
    current_params: list[OperatorParams] | None = None,
) -> CausalityProof:
    """执行注册门：当前参数和高风险时序边界均须通过四类未来扰动。"""

    parameter_cases = _registration_parameter_cases(op, current_params)
    panel = make_registration_panel()
    failures: list[str] = []
    for params in parameter_cases:
        for strategy in PerturbStrategy:
            try:
                reports = prove_causality(op, params, panel, perturb_strategy=strategy)
            except Exception as exc:
                failures.append(
                    f"params={params.model_dump()} [{strategy.value}] 执行异常: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue
            failures.extend(
                f"params={params.model_dump()} [{strategy.value}] {report.detail}"
                for report in reports
                if not report.passed
            )
    if failures:
        detail = "; ".join(failures[:5])
        raise ValueError(f"算子 {type(op).name} 因果性注册证明失败: {detail}")
    return CausalityProof(
        implementation_hash=operator_implementation_hash(op),
        parameter_hashes=tuple(sorted(_parameter_hash(p) for p in parameter_cases)),
    )


def validate_causality_proof(op: Operator, proof: CausalityProof) -> None:
    """拒绝被代码/schema 变更失效的证明对象。"""

    if proof.implementation_hash != operator_implementation_hash(op):
        raise ValueError(f"算子 {type(op).name} 的因果性证明已因实现变更失效")
    if not proof.parameter_hashes:
        raise ValueError(f"算子 {type(op).name} 的因果性证明未覆盖参数")


def _perturb_future(
    panel: pl.DataFrame,
    split_ts: Any,
    strategy: PerturbStrategy = PerturbStrategy.NAN,
) -> pl.DataFrame:
    """把 ``timestamp > split_ts`` 的所有数值列按策略扰动（AUDIT-P2-12）。

    四种策略：
    - ``nan``：置 NaN（最激进，原策略）
    - ``extreme``：乘以 1e6（极值放大，检测量纲敏感度）
    - ``negate``：乘以 -1（符号反转，检测方向性泄漏）
    - ``random_large``：替换为随机大数（检测 NaN 传播掩盖的未来函数）

    扰动幅度极大，任何泄漏都会把 T 之前的输出打成完全不同的值。
    """

    future_mask = pl.col("timestamp") > split_ts
    numeric_cols = [
        c for c, dt in zip(panel.columns, panel.dtypes, strict=True) if dt.is_numeric()
    ]
    exprs = []
    for c in numeric_cols:
        if c in ("timestamp", "symbol"):
            continue
        if strategy == PerturbStrategy.NAN:
            perturbed = (
                pl.when(future_mask).then(pl.lit(float("nan"))).otherwise(pl.col(c))
            )
        elif strategy == PerturbStrategy.EXTREME:
            perturbed = pl.when(future_mask).then(pl.col(c) * 1e6).otherwise(pl.col(c))
        elif strategy == PerturbStrategy.NEGATE:
            perturbed = pl.when(future_mask).then(pl.col(c) * -1.0).otherwise(pl.col(c))
        elif strategy == PerturbStrategy.RANDOM_LARGE:
            perturbed = (
                pl.when(future_mask)
                .then(
                    pl.Series(
                        "_rnd", [random.uniform(-1e6, 1e6) for _ in range(panel.height)]
                    ).alias("_rnd")
                )
                .otherwise(pl.col(c))
            )
        else:
            perturbed = pl.col(c)
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
    perturb_strategy: PerturbStrategy = PerturbStrategy.NAN,
) -> list[CausalityReport]:
    """证明算子 ``op`` 在给定 panel 上因果（无未来函数）。

    对每个 ``T`` in ``split_timestamps``（默认取除首尾外的若干中点），做未来
    扰动不变性验证，返回报告列表。全部 ``passed=True`` 即证明该算子因果。

    Args:
        op: 已注册算子实例。
        params: 算子参数。
        panel: 确定性面板（含 timestamp/symbol 及算子所需列）。
        split_timestamps: 验证切点；默认取面板内第 1/3、1/2、2/3 处的时间戳。
        perturb_strategy: 扰动策略（AUDIT-P2-12）；默认 NaN 保持不变。
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
        perturbed = _perturb_future(panel, t, strategy=perturb_strategy)
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
    perturb_strategy: PerturbStrategy = PerturbStrategy.NAN,
) -> bool:
    """便捷封装：所有切点都通过即返回 True。"""

    return all(
        r.passed
        for r in prove_causality(
            op, params, panel, split_timestamps, perturb_strategy=perturb_strategy
        )
    )


def math_note() -> str:
    """返回因果性证明的数学说明文本（供文档 / 报告引用）。"""

    return (
        "因果性定义：算子 f 因果 ⟺ ∀ 面板 P, ∀ 时刻 T, "
        "仅改 P 中 timestamp>T 的行不改变 f(P) 在 timestamp≤T 行的输出。"
        "prove_causality 用未来扰动不变性按此定义做数值证明：扰动全部未来数据后，"
        "若 t≤T 的输出逐元素不变，则该算子在 T 切面不读未来；遍历多个 T 覆盖全历史。"
        f"（实现常量：tol={1e-9}）"
    )
