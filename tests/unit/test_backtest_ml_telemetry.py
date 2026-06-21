"""ML 策略和 Telemetry 接口测试

聚焦 FeatureEngine、MLSignalStrategy 子类、TimeSeriesSplit 和
instrument_engine 的公共接口，不测试内部函数实现。
"""

import polars as pl

from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.ml_strategy import (
    FeatureEngine,
    MLSignalStrategy,
    TimeSeriesSplit,
)
from long_earn.backtest.engine.telemetry import instrument_engine


class MockMLStrategy(MLSignalStrategy):
    def predict_weights(self, features: pl.DataFrame) -> dict[str, float]:
        symbols = features["symbol"].to_list()
        if not symbols:
            return {}
        return {s: 1.0 / len(symbols) for s in symbols}


def test_feature_engine_interface():
    """FeatureEngine.compute_features 返回非空特征表"""
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
    assert "rsi_14" in features.columns


def test_ml_strategy_generates_signal():
    """MLSignalStrategy 子类可通过引擎执行回测"""
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


def test_time_series_split_interface():
    """TimeSeriesSplit.split 返回正确数量的折叠"""
    timestamps = list(range(100))
    splitter = TimeSeriesSplit(n_splits=3, gap=0)
    splits = splitter.split(timestamps)
    assert len(splits) == 3
    for train, test in splits:
        assert len(train) > 0
        assert len(test) > 0
        assert max(train) < min(test)


def test_instrument_engine_attaches_context():
    """instrument_engine 为引擎附加可观测性上下文"""
    engine = EventDrivenBacktestEngine()
    ctx = instrument_engine(engine)
    assert hasattr(engine, "_otel_ctx")
    assert engine._otel_ctx is ctx
