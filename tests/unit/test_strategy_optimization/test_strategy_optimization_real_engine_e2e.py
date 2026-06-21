"""策略优化真实引擎 e2e：optimize → 真实回测引擎 → 验收。

用真实 EventDrivenBacktestEngine + DSLStrategy（算子目录路径）跑基线与优化版，
AcceptanceGate 基于真实回测指标判定。证明优化版在真实引擎里确实业绩更优并被接受。

构造：A 强势上行 / B 持平 / C 下行。
- 基线：反向动量（ascending=true，选最差动量 → 持有下行的 C）。
- 优化：正向动量（ascending=false，选最强动量 → 持有上行的 A）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import polars as pl

from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.dsl import parse_strategy_yaml
from long_earn.services.backtest_service import DSLStrategy
from long_earn.strategy_optimization import (
    AcceptanceGate,
    FakeStrategyOptimizer,
    OptimizationPipeline,
)

SYMBOLS = ["A.SZ", "B.SH", "C.SZ"]
START, END = "2024-01-01", "2024-02-15"

# 基线/优化只差 rank 的 ascending：true 选最差动量（劣），false 选最强动量（优）。
_YAML_TEMPLATE = """
strategy:
  name: {name}
  universe: {{ type: csi300 }}
  start_date: {start}
  end_date: {end}
  operator_factors:
    - op: returns
      alias: mom
      params: {{ field: close, period: 5 }}
  signals:
    - type: operator
      op: rank_top
      params: {{ field: mom, top: 1, ascending: {ascending} }}
  weights: {{ method: equal }}
"""


def _yaml(name: str, ascending: bool) -> str:
    return _YAML_TEMPLATE.format(
        name=name, start=START, end=END, ascending="true" if ascending else "false"
    )


BASE_YAML = _yaml("WorstMomentum", ascending=True)
OPTIMIZED_YAML = _yaml("BestMomentum", ascending=False)


def _trending_panel() -> pl.DataFrame:
    rows = []
    base = datetime(2024, 1, 1)
    for i in range(45):
        ts = base + timedelta(days=i)
        for sym, growth in [("A.SZ", 1.008), ("B.SH", 1.0005), ("C.SZ", 0.992)]:
            close = round(10.0 * (growth ** i), 4)
            rows.append(
                {
                    "timestamp": ts, "symbol": sym, "open": close,
                    "high": close * 1.005, "low": close * 0.995,
                    "close": close, "volume": 10000.0,
                }
            )
    return pl.DataFrame(rows)


class _RealEngineBacktest:
    """用真实引擎跑策略 YAML，返回 BacktestService.run 同形 dict。"""

    def __init__(self, panel: pl.DataFrame, provider_cls) -> None:
        self._panel = panel
        self._provider_cls = provider_cls
        self.calls = 0

    def run(self, strategy_yaml: str = "", start_date: str = "", end_date: str = "") -> dict[str, Any]:
        self.calls += 1
        dsl = parse_strategy_yaml(strategy_yaml)
        engine = EventDrivenBacktestEngine(data_provider=self._provider_cls(self._panel))
        result = engine.run(
            DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl), START, END, SYMBOLS
        )
        if not result.success:
            return {"error": result.message, "strategy_diagnostics": {"degenerate": True}}
        return {
            "total_return": result.total_return,
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown": result.max_drawdown,
            "trade_count": result.trade_count or 0,
            "strategy_diagnostics": {"degenerate": (result.trade_count or 0) == 0},
        }


class TestStrategyOptimizationRealEngineE2E:
    def test_optimization_accepted_via_real_engine(self, mock_data_provider):
        """真实引擎：优化版正向动量 sharpe 优于反向动量基线 → 验收通过。"""
        backtest = _RealEngineBacktest(_trending_panel(), mock_data_provider)
        baseline = backtest.run(BASE_YAML)
        assert not baseline.get("error"), baseline

        optimizer = FakeStrategyOptimizer(
            mutator=lambda base, _s: {
                **base,
                "strategy_name": "BestMomentum",
                "evolution_lineage": [{"from": "WorstMomentum"}],
            }
        )
        outcome = OptimizationPipeline(optimizer, backtest, gate=AcceptanceGate()).run(
            base_strategy={"strategy_name": "WorstMomentum"},
            base_strategy_yaml=OPTIMIZED_YAML,
            improvement_suggestions=["改用正向动量，规避下行标的"],
            baseline_backtest=baseline,
        )

        assert outcome.accepted is True, (
            f"应被接受：{outcome.acceptance.reason}；"
            f"baseline={outcome.acceptance.baseline_sharpe}, "
            f"optimized={outcome.acceptance.optimized_sharpe}"
        )
        assert outcome.lineage_depth == 1
        assert backtest.calls == 2  # 基线 + 优化版两次真实回测

    def test_rejected_when_optimization_not_better(self, mock_data_provider):
        """优化版 = 基线（指标相同）→ sharpe 未严格提升 → 拒绝。"""
        backtest = _RealEngineBacktest(_trending_panel(), mock_data_provider)
        baseline = backtest.run(BASE_YAML)
        optimizer = FakeStrategyOptimizer(
            mutator=lambda base, _s: {**base, "strategy_name": "Same", "evolution_lineage": []}
        )
        outcome = OptimizationPipeline(optimizer, backtest, gate=AcceptanceGate()).run(
            base_strategy={"strategy_name": "EqualAll"},
            base_strategy_yaml=BASE_YAML,
            improvement_suggestions=[],
            baseline_backtest=baseline,
        )
        assert outcome.accepted is False
