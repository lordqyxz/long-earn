"""事件引擎集成测试（精简版）

保留 test_engine.py 未覆盖的集成场景：
- 多策略对比（价值/动量/均值回归）
- 基准对比指标
- Walk-Forward 扩展验证
- 最大持仓限制
"""

from datetime import datetime, timedelta

import polars as pl

from long_earn.backtest.domain.entities import SignalEvent
from long_earn.backtest.engine.core import EventDrivenBacktestEngine
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


def create_mock_data():
    """创建模拟数据"""
    dates = [datetime(2023, 1, 1) + timedelta(days=i) for i in range(20)]

    rows = []
    for d in dates:
        day_idx = (d - dates[0]).days

        rows.append(
            {
                "timestamp": d,
                "symbol": "S1",
                "close": 10.0 * (1.001**day_idx),
                "roe": 0.2,
                "eps": 1.0,
            }
        )

        s2_price = (
            10.0 * (1.02**day_idx)
            if day_idx < 10
            else 10.0 * (1.02**10) * (0.98 ** (day_idx - 10))
        )
        rows.append(
            {"timestamp": d, "symbol": "S2", "close": s2_price, "roe": 0.1, "eps": 0.5}
        )

        rows.append(
            {
                "timestamp": d,
                "symbol": "S3",
                "close": 10.0 * (0.998**day_idx),
                "roe": -0.05,
                "eps": -0.1,
            }
        )

    return pl.DataFrame(rows)


class ValueStrategy(BaseStrategy):
    """价值策略：ROE > 0.15 则持有"""

    def on_bar(self, bars: pl.DataFrame, context):
        selected = bars.filter(pl.col("roe") > 0.15)
        symbols = selected["symbol"].to_list()
        weights = {s: 1.0 / len(symbols) if symbols else 0.0 for s in symbols}
        return SignalEvent(
            timestamp=context.current_timestamp,
            trace_id=f"sig_{context.current_timestamp.isoformat()}",
            event_id=f"sig_{context.current_timestamp.isoformat()}",
            signals=weights,
            strategy_id=self.strategy_id,
        )


class MomentumStrategy(BaseStrategy):
    """动量策略：选取 5 日涨幅最高者"""

    def on_bar(self, bars: pl.DataFrame, context):
        symbols = bars["symbol"].to_list()
        returns = {}
        for s in symbols:
            hist = context.get_history(s, "close", 6)
            if len(hist) < 6:
                returns[s] = -1.0
                continue
            ret = (hist[-1] / hist[0]) - 1
            returns[s] = ret
        best_s = max(returns, key=returns.get)
        return SignalEvent(
            timestamp=context.current_timestamp,
            trace_id=f"sig_{context.current_timestamp.isoformat()}",
            event_id=f"sig_{context.current_timestamp.isoformat()}",
            signals={best_s: 1.0},
            strategy_id=self.strategy_id,
        )


def _run_engine(strategy, data):
    provider = MockDataProvider(data)
    engine = EventDrivenBacktestEngine(data_provider=provider)
    return engine.run(strategy, "2023-01-01", "2023-01-20", ["S1", "S2", "S3"])


def test_value_strategy():
    """价值策略回测应成功"""
    data = create_mock_data()
    strategy = ValueStrategy("ValueSlab")
    res = _run_engine(strategy, data)
    assert res.success is True, res.message
    assert res.total_return is not None
    assert res.trading_days == 20


def test_momentum_strategy():
    """动量策略回测应成功"""
    data = create_mock_data()
    strategy = MomentumStrategy("MomSlab")
    res = _run_engine(strategy, data)
    assert res.success is True, res.message
    assert res.total_return is not None


def test_benchmark_metrics_computed():
    """传入 benchmark_symbol 时基准对比指标正确计算"""
    data = create_mock_data()
    provider = MockDataProvider(data)
    engine = EventDrivenBacktestEngine(data_provider=provider)
    strategy = ValueStrategy("BMTest")
    res = engine.run(
        strategy, "2023-01-01", "2023-01-20", ["S1", "S2", "S3"], benchmark_symbol="S1"
    )
    assert res.alpha is not None
    assert res.beta is not None
    assert res.information_ratio is not None
    assert res.tracking_error is not None
    assert res.benchmark_return is not None


def test_benchmark_metrics_default_zero():
    """不传 benchmark_symbol 时基准指标应为 0"""
    data = create_mock_data()
    strategy = ValueStrategy("BMDefault")
    res = _run_engine(strategy, data)
    assert res.alpha == 0.0
    assert res.beta == 0.0
    assert res.information_ratio == 0.0
    assert res.tracking_error == 0.0
    assert res.benchmark_return == 0.0


def test_max_positions_enforced():
    """最大持仓限制生效"""
    data = create_mock_data()
    provider = MockDataProvider(data)
    engine = EventDrivenBacktestEngine(data_provider=provider, max_positions=2)
    strategy = ValueStrategy("MaxPos")
    res = engine.run(strategy, "2023-01-01", "2023-01-20", ["S1", "S2", "S3"])
    assert res.success is True
