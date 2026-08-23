"""算子因果性（无未来函数）证明测试。

每个注册算子用 :func:`prove_causality` 做**未来扰动不变性**验证：扰动全部
``timestamp > T`` 的数据后，若 ``t <= T`` 输出逐元素不变，则该算子在 T 切面
不读未来。凡进算子目录的算子必须通过——金融级可信硬约束。

AUDIT-P2-12：四种扰动策略（NaN / 极值 / 负数 / 随机大数）全覆盖，
防止算子通过 NaN 传播吞掉未来数据泄漏。
"""

from __future__ import annotations

import polars as pl
import pytest

from long_earn.backtest.operators import OPERATOR_REGISTRY, list_operators
from long_earn.backtest.operators.causality import (
    PerturbStrategy,
    is_causal,
    math_note,
    prove_causality,
)
from long_earn.backtest.operators.compose.arithmetic import ArithmeticParams
from long_earn.backtest.operators.compose.lowvol_momentum_combo import (
    LowvolMomentumComboParams,
)
from long_earn.backtest.operators.compose.quality_momentum import QualityMomentumParams
from long_earn.backtest.operators.factor.e2e_volatility import (
    P as E2EVolatilityParams,
)
from long_earn.backtest.operators.factor.gross_margin_stability import (
    GrossMarginStabilityParams,
)
from long_earn.backtest.operators.factor.log_return import LogReturnParams
from long_earn.backtest.operators.factor.realized_vol import RealizedVolParams
from long_earn.backtest.operators.factor.returns import ReturnsParams
from long_earn.backtest.operators.factor.roe_quality import RoeQualityParams
from long_earn.backtest.operators.factor.shift import ShiftParams
from long_earn.backtest.operators.factor.windowed import WindowedParams
from long_earn.backtest.operators.filter.threshold import FilterThresholdParams
from long_earn.backtest.operators.rank.topn import RankTopParams
from long_earn.backtest.operators.technical.bollinger import BollingerParams
from long_earn.backtest.operators.technical.macd import MACDParams
from long_earn.backtest.operators.technical.rsi import RSIParams
from long_earn.backtest.operators.technical.sma_ema import EMAParams, SMAParams

# 每个算子 + 一组合法参数。factor/technical 返回 Series；rank/compose 返回
# DataFrame；causality prover 两者都处理。
PARAM_CASES = [
    ("shift", ShiftParams(field="close", periods=5)),
    ("returns", ReturnsParams(field="close", period=5)),
    ("windowed", WindowedParams(field="close", window=10, agg="mean")),
    ("windowed", WindowedParams(field="close", window=10, agg="std")),
    ("filter_threshold", FilterThresholdParams(field="close", op=">", value=15.0)),
    ("rank_top", RankTopParams(field="close", top=2, ascending=False)),
    ("arithmetic", ArithmeticParams(lhs="high", rhs="low", op="-", alias="spread")),
    ("sma", SMAParams(field="close", window=10)),
    ("ema", EMAParams(field="close", span=8)),
    ("rsi", RSIParams(field="close", window=14)),
    ("macd", MACDParams(field="close", fast=5, slow=12, signal=3)),
    ("bollinger", BollingerParams(field="close", window=15, k=2.0)),
    # operator_dev 自主研发写盘算子
    ("log_return", LogReturnParams(field="close", period=5)),
    ("realized_vol", RealizedVolParams(field="close", window=10)),
    # operator_dev 新增算子（htr_subgraph 接入后由 LLM 生成）
    (
        "gross_margin_stability",
        GrossMarginStabilityParams(field="close", window=60, min_samples=30, eps=1e-10),
    ),
    ("roe_quality", RoeQualityParams(field="close", window=20, min_samples=5)),
    (
        "lowvol_momentum_combo",
        LowvolMomentumComboParams(
            field="close",
            low_vol_lookback=20,
            momentum_lookback=20,
            low_vol_weight=0.7,
            momentum_weight=0.3,
            min_obs=5,
        ),
    ),
    (
        "quality_momentum",
        QualityMomentumParams(field="close", momentum_window=20, quality_window=60),
    ),
    ("e2e_volatility", E2EVolatilityParams(field="close", window=10)),
]

# AUDIT-P2-12：四种扰动策略全覆盖
PERTURB_STRATEGIES = list(PerturbStrategy)


def test_math_note_documents_definition():
    """因果性证明基于明确的数学定义，非经验拟合。"""
    assert "因果性定义" in math_note()
    assert "timestamp>T" in math_note()


@pytest.mark.parametrize("op_name,params", PARAM_CASES)
@pytest.mark.parametrize("strategy", PERTURB_STRATEGIES)
def test_operator_is_causal(
    op_name: str, params, strategy: PerturbStrategy, panel: pl.DataFrame
):
    """每个算子 × 每种扰动策略都必须通过未来扰动不变性证明（AUDIT-P2-12）。"""
    reports = prove_causality(
        OPERATOR_REGISTRY[op_name], params, panel, perturb_strategy=strategy
    )
    failed = [r for r in reports if not r.passed]
    assert not failed, f"{op_name}[{strategy.value}] 因果性证明失败：\n" + "\n".join(
        f"  T={r.split_timestamp}: {r.detail}" for r in failed
    )


def test_all_catalog_operators_covered_by_causality_suite():
    """新增算子必须显式登记因果性测试用例，杜绝漏测。"""
    untested = set(list_operators()) - {name for name, _ in PARAM_CASES}
    assert not untested, f"未登记因果性用例的算子: {sorted(untested)}"


@pytest.mark.parametrize("strategy", PERTURB_STRATEGIES)
def test_causality_prover_detects_future_leak(
    strategy: PerturbStrategy, panel: pl.DataFrame
):
    """负向：构造读未来的算子（shift(-1)），prover 必须检出（AUDIT-P2-12）。"""
    from long_earn.backtest.operators._util import temporal_series
    from long_earn.backtest.operators.base import Operator, OperatorParams, operator

    @operator
    class _FutureLeak(Operator):
        name = "_test_future_leak"
        category = "factor"
        params_cls = OperatorParams

        def apply(self, panel, params):  # type: ignore[no-untyped-def]
            return temporal_series(
                panel, pl.col("close").shift(-1).over("symbol").alias("leak")
            )

    OPERATOR_REGISTRY["_test_future_leak"] = _FutureLeak()
    try:
        assert not is_causal(
            OPERATOR_REGISTRY["_test_future_leak"],
            OperatorParams(),
            panel,
            perturb_strategy=strategy,
        ), f"prover[{strategy.value}] 未能检出未来函数泄漏"
    finally:
        OPERATOR_REGISTRY.pop("_test_future_leak", None)

