"""算子目录 DSL 执行路径的因果性（无未来函数）证明。

未来扰动不变性：跑一次回测得权益曲线 E1；把回测区间后半段价格大幅扰动（模拟
"未来被改写"），再跑得 E2；断言前半段逐日权益不变。若不变，则前半段交易决策
不依赖后半段（未来）数据——无未来函数。容差 abs=1.0/rel=1e-6 严格介于浮点
噪声（~1e-7）与真实泄漏（O(1000)）之间。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.dsl import parse_strategy_yaml
from long_earn.services.backtest_service import DSLStrategy

SYMBOLS = ["A.SZ", "B.SH", "C.SZ", "D.SZ"]
START, END = "2024-01-01", "2024-02-15"

OPERATOR_YAML = f"""
strategy:
  name: OpCausal
  universe: {{ type: csi300 }}
  start_date: {START}
  end_date: {END}
  operator_factors:
    - op: returns
      alias: mom
      params: {{ field: close, period: 5 }}
  signals:
    - type: operator
      op: filter_threshold
      params: {{ field: mom, op: ">", value: 0.0 }}
    - type: operator
      op: rank_top
      params: {{ field: mom, top: 2, ascending: false }}
  weights: {{ method: equal }}
"""


def _make_panel(perturb_after: datetime | None = None) -> pl.DataFrame:
    """确定性面板；给 perturb_after 时把之后的价格 ×1e3 + 噪声（模拟未来被改写）。"""
    rng = np.random.default_rng(42)
    rows = []
    base = datetime(2024, 1, 1)
    for i in range(45):
        ts = base + timedelta(days=i)
        for s_idx, sym in enumerate(SYMBOLS):
            close = 10.0 * (1.004 - 0.002 * s_idx) ** i + 0.05 * rng.standard_normal()
            if perturb_after is not None and ts > perturb_after:
                close = close * 1e3 + 100.0 * rng.standard_normal()
            rows.append(
                {
                    "timestamp": ts, "symbol": sym, "open": close,
                    "high": close * 1.01, "low": close * 0.99,
                    "close": round(float(close), 4), "volume": 10000.0,
                }
            )
    return pl.DataFrame(rows)


def _equity_curve(panel: pl.DataFrame, mock_data_provider) -> list:
    dsl = parse_strategy_yaml(OPERATOR_YAML)
    engine = EventDrivenBacktestEngine(data_provider=mock_data_provider(panel))
    result = engine.run(
        DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl), START, END, SYMBOLS
    )
    assert result.success, result.message
    return list(result.daily_returns or [])


class TestOperatorDslCausality:
    def test_operator_dsl_no_future_function(self, mock_data_provider):
        split = datetime(2024, 1, 24)  # 约中点
        e1 = _equity_curve(_make_panel(), mock_data_provider)
        e2 = _equity_curve(_make_panel(perturb_after=split), mock_data_provider)

        split_idx = sum(
            1 for t in _make_panel()["timestamp"].unique().to_list() if t <= split
        )
        front1, front2 = e1[:split_idx], e2[:split_idx]
        assert len(front1) == len(front2)
        for i, (a, b) in enumerate(zip(front1, front2, strict=True)):
            va = a["value"] if isinstance(a, dict) else a
            vb = b["value"] if isinstance(b, dict) else b
            assert va == pytest.approx(vb, rel=1e-6, abs=1.0), (
                f"前半段第 {i} 日权益因未来扰动而改变（含未来函数）：{va} != {vb}"
            )

    def test_operator_dsl_does_use_operator_path(self):
        assert parse_strategy_yaml(OPERATOR_YAML).has_operator_steps()
