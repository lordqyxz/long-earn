"""算子目录 DSL 端到端测试：算子目录接入策略执行路径。

策略 YAML 用算子名+参数（``operator_factors`` + ``type: operator`` 信号步骤）
描述，DSLStrategy 走算子目录执行路径（绕过旧表达式求值器），经引擎回测产生
真实交易。另验证解析期校验（未知 op / 坏参数 / 缺 op 在 parse 阶段抛错）与
旧表达式路径向后兼容。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import pytest

from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.dsl import parse_strategy_yaml
from long_earn.services.backtest_service import DSLStrategy

SYMBOLS = ["A.SZ", "B.SH", "C.SZ"]

# 算子目录 DSL：returns 算动量 → filter 正动量 → rank_top 取前 2
OPERATOR_YAML = """
strategy:
  name: OperatorMomentum
  description: 算子目录动量策略
  universe: { type: csi300 }
  start_date: 2024-01-01
  end_date: 2024-01-30
  operator_factors:
    - op: returns
      alias: mom
      params: { field: close, period: 5 }
  signals:
    - type: operator
      op: filter_threshold
      params: { field: mom, op: ">", value: 0.0 }
    - type: operator
      op: rank_top
      params: { field: mom, top: 2, ascending: false }
  weights: { method: equal }
"""


def _trending_panel() -> pl.DataFrame:
    """A/B 上行、C 下行，便于动量选股产生交易。"""
    rows = []
    base = datetime(2024, 1, 1)
    for i in range(30):
        ts = base + timedelta(days=i)
        for sym, growth in [("A.SZ", 1.005), ("B.SH", 1.003), ("C.SZ", 0.997)]:
            close = round(10.0 * (growth ** i), 4)
            rows.append(
                {
                    "timestamp": ts, "symbol": sym, "open": close,
                    "high": close * 1.01, "low": close * 0.99,
                    "close": close, "volume": 10000.0,
                }
            )
    return pl.DataFrame(rows)


class TestOperatorDslE2E:
    def test_operator_strategy_runs_and_trades(self, mock_data_provider):
        """算子目录策略经引擎回测成功并产生交易。"""
        dsl = parse_strategy_yaml(OPERATOR_YAML)
        engine = EventDrivenBacktestEngine(data_provider=mock_data_provider(_trending_panel()))
        result = engine.run(
            DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl),
            "2024-01-01", "2024-01-30", SYMBOLS,
        )
        assert result.success, result.message
        assert (result.trade_count or 0) > 0

    def test_parse_rejects_unknown_operator(self):
        with pytest.raises(ValueError, match=r"nonexistent_op|未知算子"):
            parse_strategy_yaml(OPERATOR_YAML.replace("op: returns", "op: nonexistent_op"))

    def test_parse_rejects_bad_params(self):
        bad = OPERATOR_YAML.replace(
            "params: { field: close, period: 5 }",
            'params: { field: close, period: "not_a_number" }',
        )
        with pytest.raises(ValueError):
            parse_strategy_yaml(bad)

    def test_parse_rejects_missing_op_in_signal(self):
        bad = OPERATOR_YAML.replace(
            "    - type: operator\n      op: filter_threshold",
            '    - type: operator\n      params: { field: mom, op: ">", value: 0.0 }',
        )
        with pytest.raises(ValueError, match="op"):
            parse_strategy_yaml(bad)

    def test_legacy_expression_path_still_works(self, mock_data_provider):
        """旧表达式 DSL 仍兼容（向后不破）。"""
        legacy = """
strategy:
  name: LegacyMomentum
  universe: { type: csi300 }
  start_date: 2024-01-01
  end_date: 2024-01-30
  factors:
    mom: "close / shift(close, 5) - 1"
  signals:
    - type: filter
      condition: mom > 0
    - type: rank
      by: mom
      ascending: false
      top: 2
  weights: { method: equal }
"""
        assert not parse_strategy_yaml(legacy).has_operator_steps()
        dsl = parse_strategy_yaml(legacy)
        engine = EventDrivenBacktestEngine(data_provider=mock_data_provider(_trending_panel()))
        assert engine.run(
            DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl),
            "2024-01-01", "2024-01-30", SYMBOLS,
        ).success
