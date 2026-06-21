"""ML 算法交易策略支持

提供 ML 策略基类、技术指标计算、特征工程辅助和样本外验证 (OOS) 支持。

技术指标包括：
- 趋势类：SMA, EMA, MACD
- 动量类：RSI, KDJ, CCI, Williams %R
- 波动类：布林带, ATR
- 量价类：OBV, 量比
"""

from typing import Any

import numpy as np
import polars as pl

from long_earn.backtest.domain.entities import SignalEvent
from long_earn.backtest.engine.strategy import BaseStrategy
from long_earn.backtest.engine.visibility import VisibilityContext

# ── 辅助函数 ──────────────────────────────────────────────────────────────


def compute_sma(series: pl.Series, window: int) -> pl.Series:
    """计算简单移动平均 (SMA)

    Args:
        series: 价格序列
        window: 窗口大小

    Returns:
        SMA 序列
    """
    return series.rolling_mean(window)


def compute_ema(series: pl.Series, span: int) -> pl.Series:
    """计算指数移动平均 (EMA)

    Args:
        series: 价格序列
        span: 指数跨度（α = 2/(span+1)）

    Returns:
        EMA 序列
    """
    return series.ewm_mean(span=span)


# ── 收益率 ────────────────────────────────────────────────────────────────


def compute_returns(series: pl.Series, period: int = 1) -> pl.Series:
    """计算收益率序列"""
    return series / series.shift(period) - 1


# ── 趋势类指标 ────────────────────────────────────────────────────────────


def compute_macd(
    series: pl.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pl.Series, pl.Series, pl.Series]:
    """计算 MACD 指标

    Returns:
        (MACD线, 信号线, 柱状图)
    """
    ema_fast = compute_ema(series, fast)
    ema_slow = compute_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ── 动量类指标 ────────────────────────────────────────────────────────────


def compute_rsi(series: pl.Series, window: int = 14) -> pl.Series:
    """计算 RSI 指标（相对强弱指数）

    取值范围 [0, 100]，通常 >70 为超买，<30 为超卖。
    """
    delta = series.diff()
    gain = delta.clip(lower_bound=0).rolling_mean(window)
    loss = (-delta).clip(lower_bound=0).rolling_mean(window)
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_kdj(  # noqa: PLR0913
    high: pl.Series,
    low: pl.Series,
    close: pl.Series,
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> tuple[pl.Series, pl.Series, pl.Series]:
    """计算 KDJ 指标（随机指标）

    基于最高价、最低价、收盘价计算，用于判断超买超卖。
    K 值 >80 超买，K 值 <20 超卖；J 值 >100 或 <0 为极端信号。

    Args:
        high: 最高价序列
        low: 最低价序列
        close: 收盘价序列
        n: RSV 周期（默认 9）
        m1: K 值平滑周期（默认 3）
        m2: D 值平滑周期（默认 3）

    Returns:
        (K, D, J) 三个 Series，取值范围 [0, 100]
    """
    # 计算 n 日内最低价和最高价
    lowest_low = low.rolling_min(n)
    highest_high = high.rolling_max(n)

    # RSV = (close - lowest) / (highest - lowest) * 100
    denom = highest_high - lowest_low
    rsv = (close - lowest_low) / denom.replace(0, np.nan) * 100

    # 用 numpy 迭代计算 K/D/J（polars 没有原生递归 EMA 对非等间距数据）
    rsv_np = rsv.to_numpy()
    k_np = np.full_like(rsv_np, 50.0, dtype=np.float64)
    d_np = np.full_like(rsv_np, 50.0, dtype=np.float64)

    alpha_k = 1.0 / m1
    alpha_d = 1.0 / m2

    first_valid = 0
    for i in range(len(rsv_np)):
        if np.isnan(rsv_np[i]):
            continue
        if first_valid == 0 and i > 0:
            first_valid = i
        if i == first_valid and first_valid > 0:
            # 第一个有效值直接用 RSV 初始化
            k_np[i] = 50.0
            d_np[i] = 50.0
            continue
        prev_k = k_np[i - 1]
        prev_d = d_np[i - 1]

        if not np.isnan(rsv_np[i]):
            k_val = prev_k + alpha_k * (rsv_np[i] - prev_k)
        else:
            k_val = prev_k
        k_np[i] = k_val
        d_np[i] = prev_d + alpha_d * (k_val - prev_d)

    # J = 3*K - 2*D
    j_np = 3.0 * k_np - 2.0 * d_np

    return (
        pl.Series("k", k_np),
        pl.Series("d", d_np),
        pl.Series("j", j_np),
    )


def compute_cci(
    high: pl.Series,
    low: pl.Series,
    close: pl.Series,
    window: int = 20,
) -> pl.Series:
    """计算 CCI 指标（商品通道指数，Commodity Channel Index）

    衡量价格相对于其统计均值的偏离程度。
    CCI > +100 表示价格显著高于均值（可能超买），CCI < -100 表示显著低于均值（可能超卖）。

    Args:
        high: 最高价序列
        low: 最低价序列
        close: 收盘价序列
        window: 计算窗口（默认 20）

    Returns:
        CCI 序列
    """
    # 典型价格 TP = (high + low + close) / 3
    tp = (high + low + close) / 3.0
    ma = tp.rolling_mean(window)
    # 平均偏差使用 numpy 计算（polars 无 rolling_mad）
    tp_np = tp.to_numpy()
    ma_np = ma.to_numpy()

    mad_np = np.full_like(tp_np, np.nan, dtype=np.float64)
    for i in range(window - 1, len(tp_np)):
        segment = tp_np[i - window + 1 : i + 1]
        mad_np[i] = np.mean(np.abs(segment - ma_np[i]))

    mad = pl.Series("mad", mad_np)
    cci = (tp - ma) / (0.015 * mad)
    return cci


def compute_williams_r(
    high: pl.Series,
    low: pl.Series,
    close: pl.Series,
    window: int = 14,
) -> pl.Series:
    """计算威廉指标（Williams %R）

    衡量收盘价在最近 n 日价格区间中的相对位置，取值范围 [-100, 0]。
    %R > -20 表示超买，%R < -80 表示超卖。

    Args:
        high: 最高价序列
        low: 最低价序列
        close: 收盘价序列
        window: 计算窗口（默认 14）

    Returns:
        威廉 %R 序列，取值范围 [-100, 0]
    """
    highest_high = high.rolling_max(window)
    lowest_low = low.rolling_min(window)
    denom = highest_high - lowest_low
    wr = (highest_high - close) / denom.replace(0, np.nan) * (-100.0)
    return wr


# ── 波动类指标 ────────────────────────────────────────────────────────────


def compute_bollinger_bands(
    series: pl.Series, window: int = 20, num_std: float = 2.0
) -> tuple[pl.Series, pl.Series, pl.Series]:
    """计算布林带

    Returns:
        (上轨, 中轨, 下轨)
    """
    mid = series.rolling_mean(window)
    std = series.rolling_std(window)
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def compute_atr(
    high: pl.Series, low: pl.Series, close: pl.Series, window: int = 14
) -> pl.Series:
    """计算 ATR (Average True Range) — 衡量市场波动性"""
    prev_close = close.shift(1).to_numpy()
    high_np = high.to_numpy()
    low_np = low.to_numpy()
    tr1 = high_np - low_np
    tr2 = np.abs(high_np - prev_close)
    tr3 = np.abs(low_np - prev_close)
    tr_np = np.maximum(np.maximum(tr1, tr2), tr3)
    tr = pl.Series("tr", tr_np)
    return tr.rolling_mean(window)


# ── 量价类指标 ────────────────────────────────────────────────────────────


def compute_obv(close: pl.Series, volume: pl.Series) -> pl.Series:
    """计算 OBV 指标（能量潮，On-Balance Volume）

    通过累积成交量来验证价格趋势：
    - 当日收盘价 > 前日收盘价：累加当日成交量
    - 当日收盘价 < 前日收盘价：减去当日成交量
    - 持平：不变化

    Args:
        close: 收盘价序列
        volume: 成交量序列

    Returns:
        OBV 序列
    """
    close_np = close.to_numpy()
    volume_np = volume.to_numpy()
    obv_np = np.zeros(len(close_np), dtype=np.float64)
    obv_np[0] = volume_np[0] if not np.isnan(volume_np[0]) else 0.0

    for i in range(1, len(close_np)):
        if np.isnan(close_np[i]) or np.isnan(volume_np[i]):
            obv_np[i] = obv_np[i - 1]
            continue
        if close_np[i] > close_np[i - 1]:
            obv_np[i] = obv_np[i - 1] + volume_np[i]
        elif close_np[i] < close_np[i - 1]:
            obv_np[i] = obv_np[i - 1] - volume_np[i]
        else:
            obv_np[i] = obv_np[i - 1]
    return pl.Series("obv", obv_np)


# ── 特征工程 ──────────────────────────────────────────────────────────────


class FeatureEngine:
    """截面特征工程：为 Slab 中的每只股票计算技术指标

    特征清单（按类别）：
    - 收益类：ret_1, ret_5, ret_20
    - 趋势类：sma_5, sma_20, sma_ratio, macd, macd_signal, macd_diff
    - 动量类：rsi_14, kdj_k, kdj_d, kdj_j, cci_20, williams_r_14
    - 波动类：volatility_20, bb_position, atr_14
    - 量价类：volume_ratio
    """

    # ── 特征工程常量 ────────────────────────────────────────
    _MIN_HISTORY = 20
    _MIN_SHORT_RETURN = 5
    _MIN_RETURN_1 = 2
    _MIN_RETURN_5 = 6
    _MIN_MEDIUM_RETURN = 20
    _MIN_RSI = 14
    _MIN_MACD = 26
    _MIN_ATR = 14
    _MIN_KDJ = 9
    _MIN_CCI = 20
    _MIN_WR = 14

    @staticmethod
    def compute_features(  # noqa: PLR0915
        slab: pl.DataFrame,
        history: pl.DataFrame,
        current_timestamp: Any,
    ) -> pl.DataFrame:
        """为当前截面计算特征矩阵

        Args:
            slab: 当前时刻截面数据 [symbol, close, ...]
            history: 全量历史数据 [timestamp, symbol, close, ...]
            current_timestamp: 当前时间戳

        Returns:
            带特征列的 DataFrame: [symbol, feature1, feature2, ...]
        """
        features_list = []
        for symbol in slab["symbol"].unique():
            sym_hist = history.filter(
                (pl.col("symbol") == symbol)
                & (pl.col("timestamp") <= current_timestamp)
            ).sort("timestamp")

            if sym_hist.is_empty() or len(sym_hist) < FeatureEngine._MIN_HISTORY:
                features_list.append({"symbol": symbol})
                continue

            close = sym_hist["close"]
            has_high_low = "high" in sym_hist.columns and "low" in sym_hist.columns
            has_volume = "volume" in sym_hist.columns
            feature_row: dict[str, Any] = {"symbol": symbol}

            # ── 收益类特征 ──
            if len(close) >= FeatureEngine._MIN_SHORT_RETURN:
                feature_row["ret_1"] = (
                    compute_returns(close, 1)[-1]
                    if len(close) >= FeatureEngine._MIN_RETURN_1
                    else 0.0
                )
                feature_row["ret_5"] = (
                    compute_returns(close, 5)[-1]
                    if len(close) >= FeatureEngine._MIN_RETURN_5
                    else 0.0
                )
            if len(close) >= FeatureEngine._MIN_MEDIUM_RETURN:
                feature_row["ret_20"] = compute_returns(close, 20)[-1]
                feature_row["volatility_20"] = close[-20:].std() or 0.0

                # 布林带位置
                upper_bb, _, lower_bb = compute_bollinger_bands(
                    close, FeatureEngine._MIN_MEDIUM_RETURN
                )
                feature_row["bb_position"] = (
                    float((close[-1] - lower_bb[-1]) / (upper_bb[-1] - lower_bb[-1]))
                    if upper_bb[-1] != lower_bb[-1]
                    else 0.5
                )

                # SMA 特征
                sma_5 = compute_sma(close, 5)
                sma_20 = compute_sma(close, FeatureEngine._MIN_MEDIUM_RETURN)
                feature_row["sma_5"] = (
                    float(sma_5[-1]) if not np.isnan(sma_5[-1]) else float(close[-1])
                )
                feature_row["sma_20"] = (
                    float(sma_20[-1]) if not np.isnan(sma_20[-1]) else float(close[-1])
                )
                feature_row["sma_ratio"] = (
                    feature_row["sma_5"] / feature_row["sma_20"]
                    if feature_row["sma_20"] != 0
                    else 1.0
                )

                # 量比特征
                if has_volume:
                    volume = sym_hist["volume"]
                    vol_ma_20 = volume[-20:].mean()
                    current_vol = float(volume[-1]) if not np.isnan(volume[-1]) else 0.0
                    feature_row["volume_ratio"] = (
                        current_vol / vol_ma_20 if vol_ma_20 and vol_ma_20 > 0 else 1.0
                    )

            # ── RSI ──
            if len(close) >= FeatureEngine._MIN_RSI:
                rsi = compute_rsi(close, FeatureEngine._MIN_RSI)
                feature_row["rsi_14"] = (
                    float(rsi[-1]) if not np.isnan(rsi[-1]) else 50.0
                )

            # ── MACD ──
            if len(close) >= FeatureEngine._MIN_MACD:
                macd_line, signal_line, _ = compute_macd(close)
                feature_row["macd"] = (
                    float(macd_line[-1]) if not np.isnan(macd_line[-1]) else 0.0
                )
                feature_row["macd_signal"] = (
                    float(signal_line[-1]) if not np.isnan(signal_line[-1]) else 0.0
                )
                feature_row["macd_diff"] = (
                    feature_row["macd"] - feature_row["macd_signal"]
                )

            # ── ATR ──
            if has_high_low and len(close) >= FeatureEngine._MIN_ATR:
                atr = compute_atr(
                    sym_hist["high"], sym_hist["low"], close, FeatureEngine._MIN_ATR
                )
                feature_row["atr_14"] = float(atr[-1]) if not np.isnan(atr[-1]) else 0.0

            # ── KDJ ──
            if has_high_low and len(close) >= FeatureEngine._MIN_KDJ + 3:
                k, d, j = compute_kdj(
                    sym_hist["high"], sym_hist["low"], close, FeatureEngine._MIN_KDJ
                )
                feature_row["kdj_k"] = float(k[-1]) if not np.isnan(k[-1]) else 50.0
                feature_row["kdj_d"] = float(d[-1]) if not np.isnan(d[-1]) else 50.0
                feature_row["kdj_j"] = float(j[-1]) if not np.isnan(j[-1]) else 50.0

            # ── CCI ──
            if has_high_low and len(close) >= FeatureEngine._MIN_CCI:
                cci = compute_cci(
                    sym_hist["high"], sym_hist["low"], close, FeatureEngine._MIN_CCI
                )
                feature_row["cci_20"] = float(cci[-1]) if not np.isnan(cci[-1]) else 0.0

            # ── Williams %R ──
            if has_high_low and len(close) >= FeatureEngine._MIN_WR:
                wr = compute_williams_r(
                    sym_hist["high"], sym_hist["low"], close, FeatureEngine._MIN_WR
                )
                feature_row["williams_r_14"] = (
                    float(wr[-1]) if not np.isnan(wr[-1]) else -50.0
                )

            features_list.append(feature_row)

        return pl.DataFrame(features_list)


class MLSignalStrategy(BaseStrategy):
    """ML 策略基类：使用特征矩阵生成交易信号的抽象接口

    子类需实现:
    - predict_weights(features: pl.DataFrame) -> dict[str, float]
    """

    def predict_weights(self, features: pl.DataFrame) -> dict[str, float]:
        """根据特征矩阵预测目标权重 (symbol -> weight)

        子类在此方法中集成 ML 模型推理逻辑。
        """
        raise NotImplementedError

    def on_bar(
        self, bars: pl.DataFrame, context: VisibilityContext
    ) -> SignalEvent | None:
        # 使用 VisibilityContext 的安全接口获取历史数据，防止数据泄漏
        # get_history_df 内部保证 timestamp <= current_timestamp
        features = FeatureEngine.compute_features(
            bars,
            context.get_history_df(),
            context.current_timestamp,
        )
        weights = self.predict_weights(features)
        if not weights:
            return None

        return SignalEvent(
            timestamp=context.current_timestamp,
            trace_id=f"ml_{context.current_timestamp.isoformat()}",
            event_id=f"ml_{context.current_timestamp.isoformat()}",
            signals=weights,
            strategy_id=self.strategy_id,
        )


class TimeSeriesSplit:
    """时序交叉验证分割器 (样本外验证 OOS)"""

    def __init__(self, n_splits: int = 3, gap: int = 0):
        self.n_splits = n_splits
        self.gap = gap

    def split(self, timestamps: list[Any]) -> list[tuple[list[Any], list[Any]]]:
        """生产 (train_timestamps, test_timestamps) 分割"""
        n = len(timestamps)
        fold_size = n // (self.n_splits + 1)
        splits = []
        for i in range(1, self.n_splits + 1):
            train_end = i * fold_size
            test_start = train_end + self.gap
            test_end = min(test_start + fold_size, n)
            splits.append((timestamps[:train_end], timestamps[test_start:test_end]))
        return splits
