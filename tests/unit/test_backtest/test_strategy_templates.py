"""策略模板单元测试

测试 DoubleMAStrategy、RSIMeanReversionStrategy、MACDHistogramStrategy
的信号逻辑正确性。
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


# ── DoubleMAStrategy ─────────────────────────────────────────────────────────


class TestDoubleMAStrategy:
    """双均线交叉策略测试"""

    def test_golden_cross_buy_signal(self):
        """金叉信号：prev sma_ratio < 1.0, curr >= 1.0 → 买入"""
        strategy = DoubleMAStrategy("test_dma")
        strategy.init()
        # 模拟上一时刻 sma_ratio 低于阈值
        strategy._state["prev_sma_ratio"] = {"000001": 0.95}
        # 当前时刻 sma_ratio 上穿阈值
        features = _make_features(sma_ratio={"000001": 1.05})

        weights = strategy.predict_weights(features)

        assert "000001" in weights
        assert weights["000001"] == 1.0
        assert strategy._state["position"] == "long"

    def test_death_cross_sell_signal(self):
        """死叉信号：prev sma_ratio >= 1.0, curr < 1.0 → 清仓"""
        strategy = DoubleMAStrategy("test_dma")
        strategy.init()
        # 模拟上一时刻 sma_ratio 高于阈值
        strategy._state["prev_sma_ratio"] = {"000001": 1.05}
        features = _make_features(sma_ratio={"000001": 0.95})

        weights = strategy.predict_weights(features)

        assert weights == {}
        assert strategy._state["position"] == "flat"

    def test_no_signal_same_side(self):
        """无变化：prev 和 curr 都在同一侧 → 返回空 dict"""
        strategy = DoubleMAStrategy("test_dma")
        strategy.init()
        # 上方无变化
        strategy._state["prev_sma_ratio"] = {"000001": 1.05}
        features_above = _make_features(sma_ratio={"000001": 1.10})
        weights = strategy.predict_weights(features_above)
        assert weights == {}

        # 下方无变化
        strategy._state["prev_sma_ratio"] = {"000001": 0.95}
        features_below = _make_features(sma_ratio={"000001": 0.92})
        weights = strategy.predict_weights(features_below)
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
        assert "C" not in weights  # C 已经在阈值之上，不是金叉
        assert sum(weights.values()) == 1.0
        # 等权分配
        assert weights["A"] == weights["B"] == 0.5

    def test_null_values_filtered(self):
        """sma_ratio 为 null 的股票应被过滤"""
        strategy = DoubleMAStrategy("test_dma")
        strategy.init()
        strategy._state["prev_sma_ratio"] = {"000001": 0.95}
        # 构造含 null 的 DataFrame
        features = pl.DataFrame(
            {
                "symbol": ["000001", "000002"],
                "sma_ratio": [1.05, None],
            }
        )

        weights = strategy.predict_weights(features)

        assert "000001" in weights
        assert "000002" not in weights


# ── RSIMeanReversionStrategy ─────────────────────────────────────────────────


class TestRSIMeanReversionStrategy:
    """RSI 均值回归策略测试"""

    def test_oversold_buy_signal(self):
        """超卖买入：rsi 从 >=30 变为 <30 → 应返回买入信号"""
        strategy = RSIMeanReversionStrategy("test_rsi")
        strategy.init()
        # 之前不在超卖区
        strategy._state["oversold_days"] = {"000001": 0}
        strategy._state["overbought_days"] = {"000001": 0}
        features = _make_features(rsi_14={"000001": 25.0})

        weights = strategy.predict_weights(features)

        assert "000001" in weights
        assert weights["000001"] == 1.0
        assert strategy._state["position"] == "long"
        assert strategy._state["oversold_days"]["000001"] == 1

    def test_overbought_sell_signal(self):
        """超买卖出：rsi 从 <=70 变为 >70 → 应返回空 dict（清仓）"""
        strategy = RSIMeanReversionStrategy("test_rsi")
        strategy.init()
        strategy._state["overbought_days"] = {"000001": 0}
        strategy._state["oversold_days"] = {"000001": 0}
        features = _make_features(rsi_14={"000001": 75.0})

        weights = strategy.predict_weights(features)

        assert weights == {}
        assert strategy._state["position"] == "flat"
        assert strategy._state["overbought_days"]["000001"] == 1

    def test_neutral_no_signal(self):
        """中性区域无信号（30-70 之间）"""
        strategy = RSIMeanReversionStrategy("test_rsi")
        strategy.init()
        strategy._state["oversold_days"] = {"000001": 0}
        strategy._state["overbought_days"] = {"000001": 0}
        features = _make_features(rsi_14={"000001": 50.0})

        weights = strategy.predict_weights(features)

        assert weights == {}
        assert strategy._state["position"] == "flat"

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
        assert "C" not in weights  # 中性区域
        assert sum(weights.values()) == 1.0
        assert weights["A"] == weights["B"] == 0.5

    def test_consecutive_oversold_tracking(self):
        """连续超卖天数追踪"""
        strategy = RSIMeanReversionStrategy("test_rsi")
        strategy.init()
        strategy._state["oversold_days"] = {"000001": 1}
        strategy._state["overbought_days"] = {"000001": 0}
        features = _make_features(rsi_14={"000001": 20.0})

        weights = strategy.predict_weights(features)

        # 已是第二天超卖，不再触发信号（new_oversold[sym] == 2 != 1）
        assert weights == {}
        assert strategy._state["oversold_days"]["000001"] == 2

    def test_oversold_recovery_resets_counter(self):
        """超卖恢复后计数归零"""
        strategy = RSIMeanReversionStrategy("test_rsi")
        strategy.init()
        strategy._state["oversold_days"] = {"000001": 3}
        strategy._state["overbought_days"] = {"000001": 0}
        features = _make_features(rsi_14={"000001": 40.0})

        strategy.predict_weights(features)

        assert strategy._state["oversold_days"]["000001"] == 0

    def test_custom_thresholds(self):
        """自定义超买超卖阈值"""
        strategy = RSIMeanReversionStrategy(
            "test_rsi", config={"oversold": 20, "overbought": 80}
        )
        strategy.init()
        strategy._state["oversold_days"] = {"000001": 0}
        strategy._state["overbought_days"] = {"000001": 0}

        # 默认 30 以下触发超卖，自定义 20 → rsi=25 不再触发
        features = _make_features(rsi_14={"000001": 25.0})
        weights = strategy.predict_weights(features)
        assert weights == {}

        # rsi=15 应触发自定义超卖
        features = _make_features(rsi_14={"000001": 15.0})
        strategy._state["oversold_days"] = {"000001": 0}
        weights = strategy.predict_weights(features)
        assert "000001" in weights


# ── MACDHistogramStrategy ────────────────────────────────────────────────────


class TestMACDHistogramStrategy:
    """MACD 柱策略测试"""

    def test_negative_to_positive_buy(self):
        """柱由负转正：macd_diff 从 <0 变为 >=0 → 买入信号"""
        strategy = MACDHistogramStrategy("test_macd")
        strategy.init()
        strategy._state["prev_diff"] = {"000001": -0.5}
        features = _make_features(macd_diff={"000001": 0.3})

        weights = strategy.predict_weights(features)

        assert "000001" in weights
        assert weights["000001"] == 1.0
        assert strategy._state["position"] == "long"

    def test_positive_to_negative_sell(self):
        """柱由正转负：macd_diff 从 >=0 变为 <0 → 空 dict"""
        strategy = MACDHistogramStrategy("test_macd")
        strategy.init()
        strategy._state["prev_diff"] = {"000001": 0.5}
        features = _make_features(macd_diff={"000001": -0.3})

        weights = strategy.predict_weights(features)

        assert weights == {}
        assert strategy._state["position"] == "flat"

    def test_no_signal_same_direction(self):
        """方向不变时无信号"""
        strategy = MACDHistogramStrategy("test_macd")
        strategy.init()

        # 持续正值
        strategy._state["prev_diff"] = {"000001": 0.3}
        weights = strategy.predict_weights(_make_features(macd_diff={"000001": 0.5}))
        assert weights == {}

        # 持续负值
        strategy._state["prev_diff"] = {"000001": -0.3}
        weights = strategy.predict_weights(_make_features(macd_diff={"000001": -0.5}))
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
        assert "C" not in weights  # C 一直是正的，不是由负转正
        assert sum(weights.values()) == 1.0
        assert weights["A"] == weights["B"] == 0.5

    def test_null_values_filtered(self):
        """macd_diff 为 null 的股票应被过滤"""
        strategy = MACDHistogramStrategy("test_macd")
        strategy.init()
        strategy._state["prev_diff"] = {"000001": -0.5}
        features = pl.DataFrame(
            {
                "symbol": ["000001", "000002"],
                "macd_diff": [0.5, None],
            }
        )

        weights = strategy.predict_weights(features)

        assert "000001" in weights
        assert "000002" not in weights

    def test_custom_threshold(self):
        """自定义阈值下的信号判定"""
        strategy = MACDHistogramStrategy("test_macd", config={"threshold": 0.1})
        strategy.init()

        # prev < 0.1, curr >= 0.1 → 买入
        strategy._state["prev_diff"] = {"000001": -0.5}
        features = _make_features(macd_diff={"000001": 0.15})
        weights = strategy.predict_weights(features)
        assert "000001" in weights

        # prev < 0.1, curr = 0.05 (小于自定义阈值 0.1) → 无信号
        strategy._state["prev_diff"] = {"000001": -0.5}
        features = _make_features(macd_diff={"000001": 0.05})
        weights = strategy.predict_weights(features)
        assert weights == {}

    def test_threshold_boundary_stable(self):
        """macd_diff 正好在阈值边界上的行为"""
        strategy = MACDHistogramStrategy("test_macd")
        strategy.init()

        # prev = -0.001, curr = 0.0 → prev < 0 and curr >= 0 → 买入
        strategy._state["prev_diff"] = {"000001": -0.001}
        features = _make_features(macd_diff={"000001": 0.0})
        weights = strategy.predict_weights(features)
        assert "000001" in weights
