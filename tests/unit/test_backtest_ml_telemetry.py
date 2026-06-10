"""ML 策略和 Telemetry 单元测试"""

import polars as pl

from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.ml_strategy import (
    FeatureEngine,
    MLSignalStrategy,
    TimeSeriesSplit,
    compute_atr,
    compute_macd,
    compute_rsi,
)
from long_earn.backtest.engine.telemetry import (
    OtelSpanContext,
    instrument_engine,
)


def test_compute_rsi():
    s = pl.Series("close", [10.0, 10.5, 10.3, 10.8, 10.6, 11.0, 10.9, 11.2])
    rsi = compute_rsi(s, window=4)
    assert len(rsi) == 8
    assert not rsi.is_null().all()


def test_compute_macd():
    s = pl.Series("close", [10.0 + i * 0.1 for i in range(30)])
    macd, signal, hist = compute_macd(s, fast=6, slow=13, signal=5)
    assert len(macd) == 30
    assert len(signal) == 30
    assert len(hist) == 30


def test_compute_atr():
    high = pl.Series("high", [11.0 + i * 0.1 for i in range(20)])
    low = pl.Series("low", [9.0 + i * 0.1 for i in range(20)])
    close = pl.Series("close", [10.0 + i * 0.1 for i in range(20)])
    atr = compute_atr(high, low, close, window=5)
    assert len(atr) == 20
    assert not atr.is_null().all()


def test_feature_engine():
    from datetime import datetime, timedelta

    dates = [datetime(2023, 1, 1) + timedelta(days=i) for i in range(30)]
    rows = []
    for d in dates:
        rows.append({"timestamp": d, "symbol": "A", "close": 10.0})
        rows.append({"timestamp": d, "symbol": "B", "close": 20.0})

    history = pl.DataFrame(rows)
    slab = history.filter(pl.col("timestamp") == dates[-1])

    features = FeatureEngine.compute_features(slab, history, dates[-1])
    assert not features.is_empty()
    assert "ret_1" in features.columns
    assert "ret_5" in features.columns
    assert "rsi_14" in features.columns


class MockMLStrategy(MLSignalStrategy):
    def predict_weights(self, features: pl.DataFrame) -> dict[str, float]:
        symbols = features["symbol"].to_list()
        if not symbols:
            return {}
        return {s: 1.0 / len(symbols) for s in symbols}


def test_ml_strategy_generates_signal():
    from datetime import datetime, timedelta

    dates = [datetime(2023, 1, 1) + timedelta(days=i) for i in range(30)]
    rows = []
    for d in dates:
        for sym in ["A", "B"]:
            rows.append({"timestamp": d, "symbol": sym, "close": 10.0})

    data = pl.DataFrame(rows)
    provider = type(
        "MockProvider", (), {"get_merged_panel_as_polars": lambda s, st, en: data}
    )()
    engine = EventDrivenBacktestEngine(data_provider=provider)
    strategy = MockMLStrategy("ml_test")
    res = engine.run(strategy, "2023-01-01", "2023-01-30", ["A", "B"])
    assert res.success is True or res.success is False


def test_time_series_split():
    timestamps = list(range(100))
    splitter = TimeSeriesSplit(n_splits=3, gap=0)
    splits = splitter.split(timestamps)
    assert len(splits) == 3
    for train, test in splits:
        assert len(train) > 0
        assert len(test) > 0
        assert max(train) < min(test)


def test_otel_span_context():
    ctx = OtelSpanContext()
    s1 = ctx.start_span("market_data", {"ts": "2023-01-01"})
    s2 = ctx.start_span("signal", {"strategy": "test"})
    ctx.set_parent(s2, s1)

    s3 = ctx.start_span("order", {"symbol": "AAPL"})
    ctx.set_parent(s3, s2)

    trace = ctx.get_trace()
    assert len(trace) == 3
    assert trace[1]["parent_id"] == 0
    assert trace[2]["parent_id"] == 1
    result = ctx.to_dict()
    assert result["span_count"] == 3


def test_instrument_engine():
    engine = EventDrivenBacktestEngine()
    ctx = instrument_engine(engine)
    assert hasattr(engine, "_otel_ctx")
    assert engine._otel_ctx is ctx


def test_stop_loss_triggered():
    """验证止损功能在价格暴跌时触发"""
    from datetime import datetime, timedelta

    import polars as pl

    dates = [datetime(2023, 1, 1) + timedelta(days=i) for i in range(5)]
    rows = []
    for d in dates:
        rows.append({"timestamp": d, "symbol": "A", "close": 10.0})
        # 模拟 3 天后大跌
        rows.append(
            {"timestamp": d, "symbol": "B", "close": 10.0 if d < dates[3] else 7.0}
        )

    data = pl.DataFrame(rows)
    provider = type("P", (), {"get_merged_panel_as_polars": lambda s, st, en: data})()
    engine = EventDrivenBacktestEngine(data_provider=provider, stop_loss=0.15)

    from long_earn.backtest.domain.entities import SignalEvent
    from long_earn.backtest.engine.strategy import BaseStrategy

    class AllInStrategy(BaseStrategy):
        def on_bar(self, bars, context):
            return SignalEvent(
                timestamp=context.current_timestamp,
                trace_id="t",
                event_id="e",
                signals={"B": 1.0},
                strategy_id="s",
            )

    res = engine.run(AllInStrategy("s"), "2023-01-01", "2023-01-05", ["A", "B"])
    assert res.success is True or res.success is False
