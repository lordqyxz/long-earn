"""策略模板库

为 LLM 策略生成提供可继承、可微调的策略模板。
每个模板继承自 MLSignalStrategy，只需实现 predict_weights() 方法。

模板清单：
- DoubleMAStrategy: 双均线交叉策略（SMA 金叉/死叉信号）
- RSIMeanReversionStrategy: RSI 均值回归策略（超卖买入、超买卖出）
- MACDHistogramStrategy: MACD 柱策略（柱由负转正买入，正转负卖出）
"""

import polars as pl

from long_earn.backtest.engine.ml_strategy import MLSignalStrategy

# ── 模板 1：双均线交叉策略 ─────────────────────────────────────────────────


class DoubleMAStrategy(MLSignalStrategy):
    """双均线交叉策略（Golden Cross / Death Cross）

    核心逻辑：
    - 金叉（短期均线上穿长期均线）→ 买入信号
    - 死叉（短期均线下穿长期均线）→ 卖出信号

    使用 FeatureEngine 输出的 sma_ratio = sma_5 / sma_20：
    - sma_ratio > 1.0 表示短期均线在长期均线之上（偏多）
    - sma_ratio 从 <1.0 变为 >1.0 时为金叉

    状态管理：
    - self._state["prev_sma_ratio"] : 记录上一时刻的 sma_ratio 值
    - self._state["position"] : 当前持仓状态（"long" / "flat"）

    可调参数（通过 config 传入）：
    - fast_window: 短期均线窗口（默认 5）
    - slow_window: 长期均线窗口（默认 20）
    - threshold: 金叉判定阈值（默认 1.0，sma_ratio 超过此值视为金叉）
    """

    def init(self) -> None:
        """初始化策略内部状态"""
        self._state["prev_sma_ratio"] = {}
        self._state["position"] = "flat"

    def predict_weights(self, features: pl.DataFrame) -> dict[str, float]:
        """基于 SMA 比率生成交易信号权重

        Args:
            features: FeatureEngine 输出的特征 DataFrame
                      必须包含 sma_ratio 列

        Returns:
            dict[symbol, weight]: 目标权重，总和 <= 1.0
            - 空仓时金叉 → weight = 1/n（等权买入）
            - 持仓时死叉 → weight = 0（清仓）
            - 无信号 → 返回空 dict（维持当前持仓）
        """
        threshold = self.config.get("threshold", 1.0)
        valid = features.filter(pl.col("sma_ratio").is_not_null())
        if valid.is_empty():
            return {}

        # 获取当前有效股票的 sma_ratio
        symbols = valid["symbol"].to_list()
        sma_ratios = valid["sma_ratio"].to_list()
        current_ratios = dict(zip(symbols, sma_ratios, strict=True))

        prev_ratios = self._state.get("prev_sma_ratio", {})

        # 检测金叉：sma_ratio 从 <threshold 变为 >= threshold
        buy_candidates = []
        for sym in symbols:
            prev = prev_ratios.get(sym, threshold - 0.01)
            curr = current_ratios.get(sym, 0.0)
            if prev < threshold and curr >= threshold:
                buy_candidates.append(sym)

        # 检测死叉：sma_ratio 从 >= threshold 变为 < threshold
        sell_candidates = []
        for sym in symbols:
            prev = prev_ratios.get(sym, threshold)
            curr = current_ratios.get(sym, threshold - 0.01)
            if prev >= threshold and curr < threshold:
                sell_candidates.append(sym)

        # 更新状态
        self._state["prev_sma_ratio"] = current_ratios

        if sell_candidates:
            self._state["position"] = "flat"
            return {}

        if buy_candidates:
            self._state["position"] = "long"
            n = len(buy_candidates)
            weight = 1.0 / n
            return dict.fromkeys(buy_candidates, weight)

        return {}


# ── 模板 2：RSI 均值回归策略 ──────────────────────────────────────────────


class RSIMeanReversionStrategy(MLSignalStrategy):
    """RSI 均值回归策略（超卖买入、超买卖出）

    核心逻辑：
    - RSI 低于 oversold 阈值 → 超卖区域，价格可能反弹 → 买入信号
    - RSI 高于 overbought 阈值 → 超买区域，价格可能回落 → 卖出信号
    - RSI 回到中性区间 → 信号消失，维持当前仓位

    可调参数（通过 config 传入）：
    - oversold: 超卖阈值（默认 30）
    - overbought: 超买阈值（默认 70）
    - lookback: 回溯窗口内至少有多少天处于超卖/超买才触发信号（默认 1）
    """

    def init(self) -> None:
        """初始化策略内部状态"""
        self._state["oversold_days"] = {}
        self._state["overbought_days"] = {}
        self._state["position"] = "flat"

    def predict_weights(self, features: pl.DataFrame) -> dict[str, float]:
        """基于 RSI 超买超卖生成交易信号权重

        Args:
            features: FeatureEngine 输出的特征 DataFrame
                      必须包含 rsi_14 列

        Returns:
            dict[symbol, weight]: 目标权重，总和 <= 1.0
            - 超卖区域 → equal weight 买入
            - 超买区域 → weight = 0（清仓）
            - 中性区域 → 空 dict（维持当前持仓）

        说明：
            可在此方法中接入 ML 模型（如 sklearn LogisticRegression）
            替代简单的阈值判断，只需将 rsi_14 等特征传入模型即可。
        """
        oversold = self.config.get("oversold", 30)
        overbought = self.config.get("overbought", 70)

        valid = features.filter(pl.col("rsi_14").is_not_null())
        if valid.is_empty():
            return {}

        rsi_values = dict(
            zip(valid["symbol"].to_list(), valid["rsi_14"].to_list(), strict=True)
        )

        prev_oversold = self._state.get("oversold_days", {})
        prev_overbought = self._state.get("overbought_days", {})

        new_oversold: dict[str, int] = {}
        new_overbought: dict[str, int] = {}
        buy_candidates: list[str] = []
        sell_candidates: list[str] = []

        for sym, rsi in rsi_values.items():
            # 统计连续超卖天数
            if rsi < oversold:
                new_oversold[sym] = prev_oversold.get(sym, 0) + 1
            else:
                new_oversold[sym] = 0

            # 统计连续超买天数
            if rsi > overbought:
                new_overbought[sym] = prev_overbought.get(sym, 0) + 1
            else:
                new_overbought[sym] = 0

            # RSI 首次从非超卖进入超卖 → 买入
            if new_oversold[sym] == 1:
                buy_candidates.append(sym)
            # RSI 首次从非超买进入超买 → 卖出
            elif new_overbought[sym] == 1:
                sell_candidates.append(sym)

        self._state["oversold_days"] = new_oversold
        self._state["overbought_days"] = new_overbought

        if sell_candidates:
            self._state["position"] = "flat"
            return {}

        if buy_candidates:
            self._state["position"] = "long"
            n = len(buy_candidates)
            weight = 1.0 / n
            return dict.fromkeys(buy_candidates, weight)

        return {}


# ── 模板 3：MACD 柱策略 ───────────────────────────────────────────────────


class MACDHistogramStrategy(MLSignalStrategy):
    """MACD 柱策略（Histogram 方向变化信号）

    核心逻辑：
    - MACD 柱（histogram）从负值转为正值 → 动能由空转多 → 买入信号
    - MACD 柱从正值转为负值 → 动能由多转空 → 卖出信号

    MACD 柱 = MACD 线 - 信号线，柱由负转正意味着快线上穿慢线（金叉），
    比传统的快慢线交叉更灵敏。

    FeatureEngine 输出的 macd_diff 列即为 MACD 柱的值。

    可调参数（通过 config 传入）：
    - threshold: 柱变化阈值（默认 0.0，柱穿过零轴即触发）
    """

    def init(self) -> None:
        """初始化策略内部状态"""
        self._state["prev_diff"] = {}
        self._state["position"] = "flat"

    def predict_weights(self, features: pl.DataFrame) -> dict[str, float]:
        """基于 MACD 柱方向变化生成交易信号权重

        Args:
            features: FeatureEngine 输出的特征 DataFrame
                      必须包含 macd_diff 列

        Returns:
            dict[symbol, weight]: 目标权重，总和 <= 1.0
            - 柱由负转正 → equal weight 买入
            - 柱由正转负 → weight = 0（清仓）
            - 方向不变 → 空 dict（维持当前持仓）

        说明：
            可在此方法中接入 ML 模型（如 LightGBM 分类器），
            将 macd_diff、rsi_14、volume_ratio 等特征组合输入模型，
            由模型输出买入/卖出/持有概率。
        """
        threshold = self.config.get("threshold", 0.0)
        valid = features.filter(pl.col("macd_diff").is_not_null())
        if valid.is_empty():
            return {}

        symbols = valid["symbol"].to_list()
        diffs = valid["macd_diff"].to_list()
        current_diffs = dict(zip(symbols, diffs, strict=True))

        prev_diffs = self._state.get("prev_diff", {})

        # 柱由负转正 → 买入
        buy_candidates = []
        for sym in symbols:
            prev = prev_diffs.get(sym, threshold - 0.01)
            curr = current_diffs.get(sym, threshold - 0.01)
            if prev < threshold and curr >= threshold:
                buy_candidates.append(sym)

        # 柱由正转负 → 卖出
        sell_candidates = []
        for sym in symbols:
            prev = prev_diffs.get(sym, threshold)
            curr = current_diffs.get(sym, threshold)
            if prev >= threshold and curr < threshold:
                sell_candidates.append(sym)

        self._state["prev_diff"] = current_diffs

        if sell_candidates:
            self._state["position"] = "flat"
            return {}

        if buy_candidates:
            self._state["position"] = "long"
            n = len(buy_candidates)
            weight = 1.0 / n
            return dict.fromkeys(buy_candidates, weight)

        return {}
