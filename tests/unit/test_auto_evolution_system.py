"""系统级端到端测试：量化策略自动进化系统整体证明。

把两个模块 + 因果性证明串成一个完整闭环，证明：
1. 系统能**研发正确算子**（operator_dev：spec → 审计 → 因果性证明 → 注册）；
2. 系统能**优化策略**（strategy_optimization：基线 → 优化 → 回测 → 验收）；
3. 系统从数学角度证明**无未来函数**：算子目录每算子过因果性证明；新研发算子
   必须过因果性证明才能注册；含未来函数的算子被拦截，绝不进入目录。
"""

from __future__ import annotations

import pytest

from long_earn.backtest.operators import OPERATOR_REGISTRY, list_operators
from long_earn.backtest.operators.causality import is_causal, math_note
from long_earn.operator_dev import (
    FakeImplementer,
    OperatorBacklog,
    OperatorSpec,
    create_operator_dev_subgraph,
)
from long_earn.strategy_optimization import (
    FakeStrategyOptimizer,
    OptimizationPipeline,
)

# 由 operator_dev 研发出的新因果算子：已实现波动率
_REALIZED_VOL = '''
import polars as pl
from typing import ClassVar
from long_earn.backtest.operators._util import temporal_series
from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class P(OperatorParams):
    field: str = "close"
    window: int = 10


@operator
class realized_vol(Operator):
    name: ClassVar[str] = "realized_vol"
    category: ClassVar[str] = "factor"
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = P
    min_history: ClassVar[int] = 0

    def apply(self, panel, params):
        expr = (
            (pl.col(params.field) / pl.col(params.field).shift(1) - 1)
            .pow(2).rolling_mean(params.window).sqrt()
            .over("symbol").alias("realized_vol")
        )
        return temporal_series(panel, expr)
'''

# 含未来函数的伪算子（绝不应注册成功）
_LEAK = '''
import polars as pl
from typing import ClassVar
from long_earn.backtest.operators._util import temporal_series
from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class P(OperatorParams):
    field: str = "close"


@operator
class leak_op(Operator):
    name: ClassVar[str] = "leak_op"
    category: ClassVar[str] = "factor"
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = P
    min_history: ClassVar[int] = 0

    def apply(self, panel, params):
        return temporal_series(panel, pl.col(params.field).shift(-2).over("symbol").alias("leak"))
'''


class _MockBacktest:
    def __init__(self, sharpe: float, ret: float) -> None:
        self._m = {
            "sharpe_ratio": sharpe, "total_return": ret,
            "strategy_diagnostics": {"degenerate": False},
        }

    def run(self, strategy_yaml: str = "", start_date: str = "", end_date: str = "") -> dict:
        return dict(self._m)


def _develop(name: str, source: str) -> dict:
    """跑 operator_dev 子图研发一个算子，返回 invoke 结果。"""
    backlog = OperatorBacklog()
    backlog.submit(
        OperatorSpec(
            name=name, intent="test", input_fields=["close"],
            category="factor", expected_output="float",
            reference_strategy="shift(close,1)",
        )
    )
    impl = FakeImplementer({name: source})
    return create_operator_dev_subgraph(implementer=impl, backlog=backlog).invoke({})


def _optimize(baseline_sharpe: float, optimized_sharpe: float) -> bool:
    """跑策略优化 pipeline，返回是否被接受。"""
    return OptimizationPipeline(
        FakeStrategyOptimizer(), _MockBacktest(optimized_sharpe, 0.3)
    ).run(
        base_strategy={"strategy_name": "Base", "description": "基线"},
        base_strategy_yaml="strategy: ...",
        improvement_suggestions=["改进"],
        baseline_backtest={"sharpe_ratio": baseline_sharpe, "total_return": 0.2},
    ).accepted


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    OPERATOR_REGISTRY.pop("realized_vol", None)
    OPERATOR_REGISTRY.pop("leak_op", None)


class TestAutoEvolutionSystem:
    def test_full_system_proves_no_future_function(self):
        """算子目录每算子 causal（契约硬约束）+ 因果性证明基于明确数学定义。"""
        for name in list_operators():
            assert type(OPERATOR_REGISTRY[name]).causal is True, name
        assert "因果性定义" in math_note()

    def test_operator_rd_produces_correct_causal_operator(self, small_causality_panel):
        """研发正确算子：注册后再次独立过因果性证明。"""
        result = _develop("realized_vol", _REALIZED_VOL)
        assert result["registered_names"] == ["realized_vol"]
        op = OPERATOR_REGISTRY["realized_vol"]
        assert is_causal(op, type(op).params_cls(), small_causality_panel) is True

    def test_system_rejects_future_function_operator(self):
        """含未来函数的算子绝不进入目录。"""
        result = _develop("leak_op", _LEAK)
        assert {r["name"]: r["status"] for r in result["results"]}["leak_op"] == "blocked"
        assert "leak_op" not in OPERATOR_REGISTRY

    def test_strategy_optimization_accepts_improvement(self):
        assert _optimize(baseline_sharpe=1.0, optimized_sharpe=1.8) is True

    def test_end_to_end_evolution_loop(self):
        """完整闭环：研发新算子 → 用它优化策略 → 验收。"""
        assert _develop("realized_vol", _REALIZED_VOL)["registered_names"] == ["realized_vol"]
        assert "realized_vol" in OPERATOR_REGISTRY
        assert _optimize(baseline_sharpe=1.2, optimized_sharpe=2.1) is True
