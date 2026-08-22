"""算子目录 DSL 端到端测试：算子目录接入策略执行路径（ADR-009 收尾）。

策略 YAML 用算子名+参数（``operator_factors`` + ``type: operator`` 信号步骤）
描述，DSLStrategy 走算子目录执行路径（旧表达式求值器已退役），经引擎回测产生
真实交易。另验证解析期校验（未知 op / 坏参数 / 缺 op 在 parse 阶段抛错）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import pytest

from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.dsl import parse_strategy_yaml
from long_earn.backtest.engine.dsl_strategy import DSLStrategy, parse_rebalance_days

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
            close = round(10.0 * (growth**i), 4)
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": sym,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 10000.0,
                }
            )
    return pl.DataFrame(rows)


class TestOperatorDslE2E:
    def test_operator_strategy_runs_and_trades(self, mock_data_provider):
        """算子目录策略经引擎回测成功并产生交易。"""
        dsl = parse_strategy_yaml(OPERATOR_YAML)
        engine = EventDrivenBacktestEngine(
            data_provider=mock_data_provider(_trending_panel())
        )
        result = engine.run(
            DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl),
            "2024-01-01",
            "2024-01-30",
            SYMBOLS,
        )
        assert result.success, result.message
        assert (result.trade_count or 0) > 0

    def test_parse_rejects_unknown_operator(self):
        with pytest.raises(ValueError, match=r"nonexistent_op|未知算子"):
            parse_strategy_yaml(
                OPERATOR_YAML.replace("op: returns", "op: nonexistent_op")
            )

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

    def test_parse_rejects_legacy_factors(self):
        """ADR-009 收尾：旧式 factors 字段必须被解析期拒绝。"""
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
  weights: { method: equal }
"""
        with pytest.raises(ValueError, match="factors"):
            parse_strategy_yaml(legacy)

    def test_parse_rejects_legacy_signal_type(self):
        """ADR-009 收尾：旧式 filter/rank/expression 信号必须被解析期拒绝。"""
        legacy = """
strategy:
  name: LegacyMomentum
  universe: { type: csi300 }
  start_date: 2024-01-01
  end_date: 2024-01-30
  signals:
    - type: filter
      condition: close > 0
  weights: { method: equal }
"""
        with pytest.raises(ValueError, match=r"type='filter'"):
            parse_strategy_yaml(legacy)


class TestParseRebalanceDays:
    """rebalance_freq 解析契约：合法格式生效，非法值退化为每日调仓。"""

    @pytest.mark.parametrize(
        ("freq", "expected"),
        [
            ("1D", 1),
            ("5D", 5),
            ("20D", 20),
            (" 10D ", 10),
            ("", 1),
            ("weekly", 1),
            ("0D", 1),
            ("-5D", 1),
            ("1.5D", 1),
        ],
    )
    def test_parse(self, freq: str, expected: int) -> None:
        assert parse_rebalance_days(freq) == expected


def _alternating_panel() -> pl.DataFrame:
    """A/B 交替领涨（每 2 天轮换），制造持续换手。

    5 日动量方向随轮换周期翻转，filter+rank_top(1) 每天选出不同领涨者：
    每日调仓时高换手，长周期调仓时换手被门控压缩。
    """
    rows = []
    base = datetime(2024, 1, 1)
    price_a, price_b = 10.0, 10.0
    for i in range(30):
        ts = base + timedelta(days=i)
        if i % 2 == 0:
            price_a *= 1.06
            price_b *= 0.94
        else:
            price_a *= 0.94
            price_b *= 1.06
        for sym, close in [("A.SZ", round(price_a, 4)), ("B.SH", round(price_b, 4))]:
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": sym,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 10000.0,
                }
            )
    return pl.DataFrame(rows)


class TestRebalanceFreqGate:
    """调仓频率门控回归：DSL 声明的 rebalance_freq 必须真实生效。

    修复前：rebalance_freq 无任何消费点，所有策略实际每日调仓，
    DSL 声明被静默忽略（rb=10 与 rb=20 回测结果完全一致）。
    """

    def test_10d_fewer_trades_than_1d(self, mock_data_provider):
        """端到端断言：调仓门控必须压缩换手（修复的直接证据）。"""

        def run_with(freq: str) -> int:
            yaml_str = OPERATOR_YAML.replace(
                "universe: { type: csi300 }",
                f"universe: {{ type: csi300, rebalance_freq: {freq} }}",
            ).replace("params: { field: mom, top: 2, ascending: false }",
                      "params: { field: mom, top: 1, ascending: false }")
            dsl = parse_strategy_yaml(yaml_str)
            engine = EventDrivenBacktestEngine(
                data_provider=mock_data_provider(_alternating_panel())
            )
            result = engine.run(
                DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl),
                "2024-01-01",
                "2024-01-30",
                ["A.SZ", "B.SH"],
            )
            assert result.success, result.message
            return result.trade_count or 0

        daily = run_with("1D")
        slow = run_with("10D")
        assert daily > 2 * slow, (
            f"10D 调仓换手应显著低于每日调仓: daily={daily}, slow={slow}"
        )

    def test_state_machine_first_bar_opens_position(self):
        """状态机：首个交易日必须建仓（不因长周期调仓而空转整个 run）。"""
        dsl = parse_strategy_yaml(
            OPERATOR_YAML.replace(
                "universe: { type: csi300 }",
                "universe: { type: csi300, rebalance_freq: 20D }",
            )
        )
        s = DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl)
        assert s._rebalance_days == 20
        assert s._should_rebalance() is True  # bar 1：建仓
        s._bar_count += 1
        assert s._should_rebalance() is False  # bar 2..20：持有
        s._bar_count = 19
        assert s._should_rebalance() is False
        s._bar_count = 20
        assert s._should_rebalance() is True  # bar 21：第二次调仓

    def test_init_resets_phase(self):
        """init() 重置调仓相位（walk-forward 复用实例时相位不漂移）。"""
        dsl = parse_strategy_yaml(
            OPERATOR_YAML.replace(
                "universe: { type: csi300 }",
                "universe: { type: csi300, rebalance_freq: 5D }",
            )
        )
        s = DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl)
        s._bar_count = 3
        s.init()
        assert s._bar_count == 0
        assert s._should_rebalance() is True
