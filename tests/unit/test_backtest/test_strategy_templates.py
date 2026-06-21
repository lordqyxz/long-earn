"""策略模板接口测试

测试 DoubleMAStrategy、RSIMeanReversionStrategy、MACDHistogramStrategy
的公共接口 predict_weights，不测试内部状态机。
"""

import polars as pl

from long_earn.backtest.engine.strategy_templates import (
    DoubleMAStrategy,
    MACDHistogramStrategy,
    RSIMeanReversionStrategy,
)


def _make_features(
    symbols: list[str] | None = None,
    sma_ratio: dict[str, float] | None = None,
    rsi_14: dict[str, float] | None = None,
    macd_diff: dict[str, float] | None = None,
) -> pl.DataFrame:
    """构造 FeatureEngine 输出格式的测试数据"""
    if symbols is None:
        symbols = ["000001"]
    rows: list[dict] = []
    for sym in symbols:
        row: dict = {"symbol": sym}
        if sma_ratio is not None:
            row["sma_ratio"] = sma_ratio.get(sym, 1.0)
        if rsi_14 is not None:
            row["rsi_14"] = rsi_14.get(sym, 50.0)
        if macd_diff is not None:
            row["macd_diff"] = macd_diff.get(sym, 0.0)
        rows.append(row)
    return pl.DataFrame(rows)


class TestDoubleMAStrategy:
    """双均线交叉策略接口测试"""

    def test_golden_cross_buy_signal(self):
        """金叉信号：prev sma_ratio < 1.0, curr >= 1.0 → 买入"""
        strategy = DoubleMAStrategy("test_dma")
        strategy.init()
        strategy._state["prev_sma_ratio"] = {"000001": 0.95}
        features = _make_features(sma_ratio={"000001": 1.05})

        weights = strategy.predict_weights(features)

        assert "000001" in weights
        assert weights["000001"] == 1.0

    def test_death_cross_sell_signal(self):
        """死叉信号：prev sma_ratio >= 1.0, curr < 1.0 → 清仓"""
        strategy = DoubleMAStrategy("test_dma")
        strategy.init()
        strategy._state["prev_sma_ratio"] = {"000001": 1.05}
        features = _make_features(sma_ratio={"000001": 0.95})

        weights = strategy.predict_weights(features)

        assert weights == {}

    def test_equal_weight_sum_is_one(self):
        """多只股票同时金叉时权重总和 = 1.0"""
        strategy = DoubleMAStrategy("test_dma")
        strategy.init()
        strategy._state["prev_sma_ratio"] = {"A": 0.95, "B": 0.95, "C": 1.05}
        features = _make_features(
            symbols=["A", "B", "C"],
            sma_ratio={"A": 1.02, "B": 1.03, "C": 1.10},
        )

        weights = strategy.predict_weights(features)

        assert "A" in weights
        assert "B" in weights
        assert sum(weights.values()) == 1.0


class TestRSIMeanReversionStrategy:
    """RSI 均值回归策略接口测试"""

    def test_oversold_buy_signal(self):
        """超卖买入：rsi < 30 → 应返回买入信号"""
        strategy = RSIMeanReversionStrategy("test_rsi")
        strategy.init()
        strategy._state["oversold_days"] = {"000001": 0}
        strategy._state["overbought_days"] = {"000001": 0}
        features = _make_features(rsi_14={"000001": 25.0})

        weights = strategy.predict_weights(features)

        assert "000001" in weights
        assert weights["000001"] == 1.0

    def test_overbought_sell_signal(self):
        """超买卖出：rsi > 70 → 应返回空 dict（清仓）"""
        strategy = RSIMeanReversionStrategy("test_rsi")
        strategy.init()
        strategy._state["overbought_days"] = {"000001": 0}
        strategy._state["oversold_days"] = {"000001": 0}
        features = _make_features(rsi_14={"000001": 75.0})

        weights = strategy.predict_weights(features)

        assert weights == {}

    def test_equal_weight_sum_is_one(self):
        """多只股票同时超卖时权重总和 = 1.0"""
        strategy = RSIMeanReversionStrategy("test_rsi")
        strategy.init()
        strategy._state["oversold_days"] = {"A": 0, "B": 0, "C": 0}
        strategy._state["overbought_days"] = {"A": 0, "B": 0, "C": 0}
        features = _make_features(
            symbols=["A", "B", "C"],
            rsi_14={"A": 25.0, "B": 28.0, "C": 50.0},
        )

        weights = strategy.predict_weights(features)

        assert "A" in weights
        assert "B" in weights
        assert sum(weights.values()) == 1.0


class TestMACDHistogramStrategy:
    """MACD 柱策略接口测试"""

    def test_negative_to_positive_buy(self):
        """柱由负转正：macd_diff 从 <0 变为 >=0 → 买入信号"""
        strategy = MACDHistogramStrategy("test_macd")
        strategy.init()
        strategy._state["prev_diff"] = {"000001": -0.5}
        features = _make_features(macd_diff={"000001": 0.3})

        weights = strategy.predict_weights(features)

        assert "000001" in weights
        assert weights["000001"] == 1.0

    def test_positive_to_negative_sell(self):
        """柱由正转负：macd_diff 从 >=0 变为 <0 → 空 dict"""
        strategy = MACDHistogramStrategy("test_macd")
        strategy.init()
        strategy._state["prev_diff"] = {"000001": 0.5}
        features = _make_features(macd_diff={"000001": -0.3})

        weights = strategy.predict_weights(features)

        assert weights == {}

    def test_equal_weight_sum_is_one(self):
        """多只股票同时反转时权重总和 = 1.0"""
        strategy = MACDHistogramStrategy("test_macd")
        strategy.init()
        strategy._state["prev_diff"] = {"A": -0.5, "B": -0.3, "C": 0.5}
        features = _make_features(
            symbols=["A", "B", "C"],
            macd_diff={"A": 0.2, "B": 0.1, "C": 0.3},
        )

        weights = strategy.predict_weights(features)

        assert "A" in weights
        assert "B" in weights
        assert sum(weights.values()) == 1.0
