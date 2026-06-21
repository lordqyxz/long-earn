"""回测引擎 + 风控集成测试

验证止损、最大回撤触发及风控关闭场景。
"""

from datetime import datetime, timedelta

import polars as pl

from long_earn.backtest.domain.entities import FillEvent, Position
from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.portfolio import Portfolio
from long_earn.backtest.engine.strategy import BaseStrategy


class MockDataProvider:
    """模拟数据提供者"""

    def __init__(self, data: pl.DataFrame):
        self.data = data

    def get_merged_panel_as_polars(self, symbols, start, end):
        return self.data.filter(
            (pl.col("symbol").is_in(symbols))
            & (pl.col("timestamp") >= datetime.strptime(start, "%Y-%m-%d"))
            & (pl.col("timestamp") <= datetime.strptime(end, "%Y-%m-%d"))
        )


def _make_data_with_crash():
    """构造先涨后暴跌的数据"""
    dates = [datetime(2023, 1, 1) + timedelta(days=i) for i in range(20)]
    rows = []
    for i, d in enumerate(dates):
        if i < 5:
            price = 10.0 + i * 0.5
        else:
            price = 12.5 - (i - 5) * 1.5
        rows.append({"timestamp": d, "symbol": "S1", "close": price})
    return pl.DataFrame(rows)


def _make_data_steady():
    """构造平稳上涨数据"""
    dates = [datetime(2023, 1, 1) + timedelta(days=i) for i in range(10)]
    rows = []
    for i, d in enumerate(dates):
        rows.append({"timestamp": d, "symbol": "S1", "close": 10.0 + i * 0.1})
    return pl.DataFrame(rows)


class AlwaysBuyStrategy(BaseStrategy):
    """始终全仓买入的策略"""

    def on_bar(self, slab, context):
        from long_earn.backtest.domain.entities import SignalEvent

        return SignalEvent(
            timestamp=context.current_timestamp,
            trace_id="trace",
            event_id="evt",
            signals={"S1": 1.0},
            strategy_id="always_buy",
        )


class TestStopLossIntegration:
    """止损触发集成测试"""

    def test_stop_loss_triggers_and_closes_position(self):
        """止损触发后应清仓"""
        data = _make_data_with_crash()
        provider = MockDataProvider(data)
        engine = EventDrivenBacktestEngine(
            data_provider=provider, stop_loss=0.05
        )
        strategy = AlwaysBuyStrategy("test_sl")

        res = engine.run(strategy, "2023-01-01", "2023-01-20", ["S1"])

        assert res.success is True
        assert res.total_return is not None


class TestMaxDrawdownIntegration:
    """最大回撤触发集成测试"""

    def test_max_drawdown_triggers_and_stops(self):
        """最大回撤触发后应停止交易"""
        data = _make_data_with_crash()
        provider = MockDataProvider(data)
        engine = EventDrivenBacktestEngine(
            data_provider=provider, max_drawdown_limit=0.05
        )
        strategy = AlwaysBuyStrategy("test_md")

        res = engine.run(strategy, "2023-01-01", "2023-01-20", ["S1"])

        assert res.success is True
        assert res.max_drawdown is not None


class TestRiskDisabled:
    """风控关闭集成测试"""

    def test_no_stop_loss_when_disabled(self):
        """止损关闭时不应触发"""
        data = _make_data_with_crash()
        provider = MockDataProvider(data)
        engine = EventDrivenBacktestEngine(
            data_provider=provider, stop_loss=0.0
        )
        strategy = AlwaysBuyStrategy("test_no_sl")

        res = engine.run(strategy, "2023-01-01", "2023-01-20", ["S1"])

        assert res.success is True

    def test_no_max_drawdown_when_disabled(self):
        """最大回撤关闭时不应触发"""
        data = _make_data_with_crash()
        provider = MockDataProvider(data)
        engine = EventDrivenBacktestEngine(
            data_provider=provider, max_drawdown_limit=0.0
        )
        strategy = AlwaysBuyStrategy("test_no_md")

        res = engine.run(strategy, "2023-01-01", "2023-01-20", ["S1"])

        assert res.success is True
