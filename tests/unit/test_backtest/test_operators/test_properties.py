"""AUDIT-P2-08: hypothesis property-based testing。

覆盖三类属性测试：
1. 算子单调性 — 输入单调变换后输出排序不变
2. 滑点对称 — 滑点始终非负，往返成本为正
3. PIT 延迟 — backward asof 不泄漏未来信息
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import polars as pl
import pytest
from hypothesis import assume, given, settings, strategies as st

from long_earn.backtest.domain.entities import ExecType, OrderEvent
from long_earn.backtest.engine.broker import Broker, TradingCostConfig
from long_earn.backtest.operators import get_operator
from long_earn.backtest.operators.factor.returns import ReturnsParams
from long_earn.backtest.operators.factor.shift import ShiftParams
from long_earn.backtest.operators.filter.threshold import FilterThresholdParams
from long_earn.backtest.operators.rank.topn import RankTopParams
from long_earn.backtest.operators.technical.sma_ema import SMAParams

# ── hypothesis 策略：生成随机价格面板 ──────────────────────────────


@st.composite
def price_panels(draw, min_symbols: int = 1, max_symbols: int = 3):
    """生成随机价格面板，含 symbol / timestamp / close / open / high / low / volume。

    约束：high >= low, open/close 在 [low, high] 内。
    """
    n_symbols = draw(st.integers(min_symbols, max_symbols))
    n_days = draw(st.integers(10, 50))
    symbols = [f"STOCK_{i:04d}" for i in range(n_symbols)]

    rows = []
    base = datetime(2024, 1, 1)
    for day in range(n_days):
        ts = base + timedelta(days=day)
        for sym in symbols:
            low = draw(st.floats(1.0, 100.0, allow_nan=False, allow_infinity=False))
            high = draw(
                st.floats(low, low + 50.0, allow_nan=False, allow_infinity=False)
            )
            close = draw(st.floats(low, high, allow_nan=False, allow_infinity=False))
            open_p = draw(st.floats(low, high, allow_nan=False, allow_infinity=False))
            volume = draw(st.floats(100.0, 1e7, allow_nan=False, allow_infinity=False))
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": sym,
                    "open": open_p,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                }
            )

    df = pl.DataFrame(rows)
    # 按 symbol + timestamp 排序（算子内部依赖此顺序）
    return df.sort(["symbol", "timestamp"])


# ── 1. 算子单调性 ─────────────────────────────────────────────────


class TestOperatorMonotonicity:
    """算子单调性：输入单调变换后，输出排序应保持不变。

    使用 hypothesis 随机生成面板，验证：
    - rank_top：所有值加常数后排序不变
    - returns：价格单调递增 → 收益率正负方向一致
    - filter_threshold：阈值平移后过滤结果等价
    - sma：输出值在窗口输入范围内
    """

    @given(price_panels(min_symbols=2, max_symbols=5))
    @settings(max_examples=200)
    def test_rank_top_ordering_invariant_under_shift(self, panel: pl.DataFrame) -> None:
        """rank_top：所有 close 加常数后 top-N 排序不变。"""
        # 价格以 1e-6 为最小报价单位，确保 +100 后不同价格不会因 IEEE-754
        # 舍入折叠为同一值。该约束保留真实的加法平移排序不变性质，且不掩盖
        # rank_top 对原始排序及相同价格 tie 的处理。
        ranking_panel = panel.with_columns(pl.col("close").round(6).alias("close"))
        top_n = max(1, panel["symbol"].n_unique() // 2)
        op = get_operator("rank_top")

        base = op.apply(
            ranking_panel, RankTopParams(field="close", top=top_n, ascending=False)
        )
        # 全部 close + 100
        shifted = ranking_panel.with_columns((pl.col("close") + 100.0).alias("close"))
        shifted_out = op.apply(
            shifted, RankTopParams(field="close", top=top_n, ascending=False)
        )

        # rank_top 返回 DataFrame，含 "rank" 列
        base_df = base.with_columns(pl.col("rank").alias("_rank"))
        shifted_df = shifted_out.with_columns(pl.col("rank").alias("_rank"))
        for ts in ranking_panel["timestamp"].unique().to_list():
            base_syms = set(
                base_df.filter(pl.col("timestamp") == ts)
                .filter(pl.col("_rank").is_not_null())["symbol"]
                .to_list()
            )
            shift_syms = set(
                shifted_df.filter(pl.col("timestamp") == ts)
                .filter(pl.col("_rank").is_not_null())["symbol"]
                .to_list()
            )
            assert base_syms == shift_syms, f"T={ts}: 排序在平移后改变"

    @given(price_panels(min_symbols=1, max_symbols=3))
    @settings(max_examples=30)
    def test_returns_sign_consistent_with_price_direction(
        self, panel: pl.DataFrame
    ) -> None:
        """returns：价格单调递增则收益率符号一致为正。"""
        op = get_operator("returns")
        out = op.apply(panel, ReturnsParams(field="close", period=1))
        df = panel.with_columns(out.alias("ret"))

        for sym in df["symbol"].unique().to_list():
            sub = df.filter(pl.col("symbol") == sym).sort("timestamp")
            for i in range(1, sub.height):
                prev_close = sub["close"][i - 1]
                curr_close = sub["close"][i]
                ret = sub["ret"][i]
                if ret is not None:
                    if curr_close > prev_close:
                        assert ret > 0, f"{sym} T={i}: close↑但 ret={ret} ≤ 0"
                    elif curr_close < prev_close:
                        assert ret < 0, f"{sym} T={i}: close↓但 ret={ret} ≥ 0"

    @given(price_panels(min_symbols=1, max_symbols=3))
    @settings(max_examples=30)
    def test_filter_threshold_equiv_under_value_shift(
        self, panel: pl.DataFrame
    ) -> None:
        """filter_threshold：阈值与字段同步平移后过滤结果不变。"""
        op = get_operator("filter_threshold")
        threshold = 20.0
        base = op.apply(
            panel, FilterThresholdParams(field="close", op=">", value=threshold)
        )
        base_df = panel.with_columns(base.alias("_pass"))

        # close + 10，阈值 + 10
        shifted = panel.with_columns((pl.col("close") + 10.0).alias("close"))
        shifted_out = op.apply(
            shifted,
            FilterThresholdParams(field="close", op=">", value=threshold + 10.0),
        )
        shifted_df = shifted.with_columns(shifted_out.alias("_pass"))

        assert base_df["_pass"].to_list() == shifted_df["_pass"].to_list(), (
            "阈值平移后过滤结果不一致"
        )

    @given(price_panels(min_symbols=1, max_symbols=3))
    @settings(max_examples=30)
    def test_shift_returns_original_value_at_lag(self, panel: pl.DataFrame) -> None:
        """shift(periods=N)：输出第 N 行后应等于原始 close 第 0 行。"""
        op = get_operator("shift")
        periods = 3
        out = op.apply(panel, ShiftParams(field="close", periods=periods))
        df = panel.with_columns(out.alias("prev"))

        for sym in df["symbol"].unique().to_list():
            sub = df.filter(pl.col("symbol") == sym).sort("timestamp")
            closes = sub["close"].to_list()
            prevs = sub["prev"].to_list()
            for i in range(periods, len(closes)):
                if prevs[i] is not None:
                    assert prevs[i] == pytest.approx(closes[i - periods], rel=1e-9), (
                        f"{sym} T={i}: shift({periods})={prevs[i]} "
                        f"≠ close[{i - periods}]={closes[i - periods]}"
                    )

    @given(price_panels(min_symbols=1, max_symbols=3))
    @settings(max_examples=30)
    def test_sma_output_bounded_by_input_range(self, panel: pl.DataFrame) -> None:
        """sma：输出值应在输入窗口 min/max 范围内。"""
        op = get_operator("sma")
        window = 5
        out = op.apply(panel, SMAParams(field="close", window=window))
        df = panel.with_columns(out.alias("sma"))

        for sym in df["symbol"].unique().to_list():
            sub = df.filter(pl.col("symbol") == sym).sort("timestamp")
            closes = sub["close"].to_list()
            smas = sub["sma"].to_list()
            for i in range(window - 1, len(closes)):
                window_vals = closes[i - window + 1 : i + 1]
                w_min = min(window_vals)
                w_max = max(window_vals)
                if smas[i] is not None:
                    assert w_min <= smas[i] <= w_max, (
                        f"{sym} T={i}: sma={smas[i]} 不在 [{w_min}, {w_max}] 内"
                    )


# ── 2. 滑点对称 ───────────────────────────────────────────────────


class TestSlippageSymmetry:
    """滑点对称性：滑点应始终非负，往返成本为正。

    使用 hypothesis 随机生成价格和订单参数，验证：
    - 滑点始终非负（成交价 ≤ 当前价 for SELL，≥ for BUY）
    - 往返成本 > 0（买入后立即卖出亏损）
    """

    @given(price_panels(min_symbols=1, max_symbols=1))
    @settings(max_examples=50)
    def test_slippage_always_non_negative(self, panel: pl.DataFrame) -> None:
        """滑点始终非负：成交价不优于当前价。"""
        broker = Broker()
        row = panel.row(panel.height // 2, named=True)
        price = float(row["close"])
        volume = float(row["volume"])
        ts = row["timestamp"]

        # 买入：成交价 ≥ 当前价（滑点向上）
        buy_order = OrderEvent(
            timestamp=ts,
            trace_id="test",
            event_id="buy_1",
            symbol=str(row["symbol"]),
            order_type="BUY",
            quantity=100,
            price=None,
            exec_type=ExecType.MARKET,
        )
        buy_fill = broker._fill_market(buy_order, price, volume)
        if buy_fill is None:
            # 参与率限制+整手取整后不足 1 手：当日无成交（P0-04 新语义），
            # 本属性只约束"有成交时"的滑点方向
            return
        assert buy_fill.fill_price >= price, (
            f"BUY fill_price={buy_fill.fill_price} < current_price={price}"
        )
        assert buy_fill.slippage >= 0, f"BUY slippage={buy_fill.slippage} < 0"

        # 卖出：成交价 ≤ 当前价（滑点向下）
        sell_order = OrderEvent(
            timestamp=ts,
            trace_id="test",
            event_id="sell_1",
            symbol=str(row["symbol"]),
            order_type="SELL",
            quantity=100,
            price=None,
            exec_type=ExecType.MARKET,
        )
        sell_fill = broker._fill_market(sell_order, price, volume)
        assert sell_fill.fill_price <= price, (
            f"SELL fill_price={sell_fill.fill_price} > current_price={price}"
        )
        assert sell_fill.slippage >= 0, f"SELL slippage={sell_fill.slippage} < 0"

    @given(price_panels(min_symbols=1, max_symbols=1))
    @settings(max_examples=50)
    def test_round_trip_slippage_positive_cost(self, panel: pl.DataFrame) -> None:
        """往返滑点为正成本：买入后立即卖出，净收益为负。"""
        broker = Broker()
        row = panel.row(panel.height // 2, named=True)
        price = float(row["close"])
        volume = float(row["volume"])
        ts = row["timestamp"]
        qty = 100

        # 买入
        buy_order = OrderEvent(
            timestamp=ts,
            trace_id="test",
            event_id="buy_1",
            symbol=str(row["symbol"]),
            order_type="BUY",
            quantity=qty,
            price=None,
            exec_type=ExecType.MARKET,
        )
        buy_fill = broker._fill_market(buy_order, price, volume)
        if buy_fill is None:
            # 参与率限制+整手取整后不足 1 手：无买入成交，往返无从谈起
            return

        # 卖出（同价）
        sell_order = OrderEvent(
            timestamp=ts,
            trace_id="test",
            event_id="sell_1",
            symbol=str(row["symbol"]),
            order_type="SELL",
            quantity=qty,
            price=None,
            exec_type=ExecType.MARKET,
        )
        sell_fill = broker._fill_market(sell_order, price, volume)
        if sell_fill is None:
            return

        # 往返净收益 = 卖出收入 - 买入支出
        buy_cost = buy_fill.fill_price * qty + buy_fill.commission
        sell_revenue = (
            sell_fill.fill_price * qty - sell_fill.commission - sell_fill.stamp_duty
        )
        net = sell_revenue - buy_cost
        assert net < 0, f"往返净收益={net:.4f} ≥ 0（滑点+佣金应产生正成本）"

    @given(price_panels(min_symbols=1, max_symbols=1))
    @settings(max_examples=50)
    def test_limit_order_slippage_direction(self, panel: pl.DataFrame) -> None:
        """限价单滑点方向正确：买入不优于限价，卖出不劣于限价。"""
        broker = Broker()
        row = panel.row(panel.height // 2, named=True)
        price = float(row["close"])
        ts = row["timestamp"]
        limit_price = price * 1.02  # 限价稍高于当前价

        # 买入限价：成交价 ≤ limit_price（不能买贵了）
        buy_order = OrderEvent(
            timestamp=ts,
            trace_id="test",
            event_id="buy_limit",
            symbol=str(row["symbol"]),
            order_type="BUY",
            quantity=100,
            price=limit_price,
            exec_type=ExecType.LIMIT,
        )
        buy_fill = broker._try_fill_limit(buy_order, price)
        if buy_fill is not None:
            assert buy_fill.fill_price <= limit_price, (
                f"BUY LIMIT fill_price={buy_fill.fill_price} > limit={limit_price}"
            )

        # 卖出限价：成交价 ≥ limit_price（不能卖便宜了）
        sell_order = OrderEvent(
            timestamp=ts,
            trace_id="test",
            event_id="sell_limit",
            symbol=str(row["symbol"]),
            order_type="SELL",
            quantity=100,
            price=limit_price * 0.98,
            exec_type=ExecType.LIMIT,
        )
        sell_fill = broker._try_fill_limit(sell_order, price)
        if sell_fill is not None:
            assert sell_fill.fill_price >= sell_order.price, (
                f"SELL LIMIT fill_price={sell_fill.fill_price} "
                f"< limit={sell_order.price}"
            )

    @given(st.floats(0.1, 100.0), st.floats(0.0, 1e8))
    @settings(max_examples=50)
    def test_impact_cost_monotonic_in_order_size(
        self, order_amount: float, daily_volume: float
    ) -> None:
        """冲击成本随订单金额单调递增。"""
        assume(daily_volume > 0)

        config = TradingCostConfig()
        impact1 = config.compute_impact_bps(order_amount, daily_volume)
        impact2 = config.compute_impact_bps(order_amount * 2, daily_volume)

        assert impact2 >= impact1, (
            f"冲击成本非单调: impact({order_amount * 2})={impact2} "
            f"< impact({order_amount})={impact1}"
        )


# ── 3. PIT 延迟 ───────────────────────────────────────────────────


class TestPITDelay:
    """PIT 延迟：backward asof 不泄漏未来信息。

    验证：
    - quarterly_to_daily_asof 只用 announce_date ≤ 当前日期的数据
    - PIT 对齐后，任何日期的财务值不来自未来公告
    """

    def test_asof_uses_announce_date_not_report_date(self) -> None:
        """PIT 对齐用 announce_date 而非 report_date：公告前不应可见。"""
        from long_earn.backtest.data.financial.panel import quarterly_to_daily_asof

        quarterly = pd.DataFrame(
            {
                "symbol": ["A"] * 3,
                "report_date": pd.to_datetime(
                    ["2024-01-15", "2024-04-15", "2024-07-15"]
                ),
                "announce_date": pd.to_datetime(
                    ["2024-02-20", "2024-05-25", "2024-08-30"]
                ),
                "roe": [10.0, 12.0, 15.0],
                "eps": [1.0, 1.2, 1.5],
            }
        )

        trading_dates = pd.date_range("2024-02-01", "2024-09-01", freq="B")
        result = quarterly_to_daily_asof(
            quarterly, ["A"], trading_dates, ["roe", "eps"]
        )

        # 2024-02-19（Q1 公告前）：roe/eps 应为 NaN
        before_announce = result.loc[
            result.index.get_level_values("date") <= pd.Timestamp("2024-02-19")
        ]
        assert before_announce["roe"].isna().all(), "Q1 公告前不应有 roe 数据"

        # 2024-02-20 及之后：应有 Q1 数据
        after_announce = result.loc[
            (result.index.get_level_values("date") >= pd.Timestamp("2024-02-20"))
            & (result.index.get_level_values("date") < pd.Timestamp("2024-05-25"))
        ]
        assert (after_announce["roe"] == 10.0).all(), "Q1 公告后应有 roe=10.0"

    def test_asof_never_uses_future_announcement(self) -> None:
        """PIT 对齐：任何日期不引用未来公告日的数据。"""
        from long_earn.backtest.data.financial.panel import quarterly_to_daily_asof

        quarterly = pd.DataFrame(
            {
                "symbol": ["B"] * 2,
                "report_date": pd.to_datetime(["2024-01-15", "2024-07-15"]),
                "announce_date": pd.to_datetime(["2024-03-01", "2024-09-01"]),
                "roe": [8.0, 20.0],
                "eps": [0.8, 2.0],
            }
        )

        trading_dates = pd.date_range("2024-01-01", "2024-12-31", freq="B")
        result = quarterly_to_daily_asof(
            quarterly, ["B"], trading_dates, ["roe", "eps"]
        )

        # 2024-03-01 到 2024-08-31：只有 Q1 数据（roe=8.0）
        mid_period = result.loc[
            (result.index.get_level_values("date") >= pd.Timestamp("2024-03-01"))
            & (result.index.get_level_values("date") <= pd.Timestamp("2024-08-31"))
        ]
        assert (mid_period["roe"] == 8.0).all(), "Q2 公告前不应有 roe=20.0"
        assert (mid_period["eps"] == 0.8).all(), "Q2 公告前不应有 eps=2.0"

        # 2024-09-01 及之后：应有 Q2 数据（roe=20.0）
        after_q2 = result.loc[
            result.index.get_level_values("date") >= pd.Timestamp("2024-09-01")
        ]
        assert (after_q2["roe"] == 20.0).all(), "Q2 公告后应有 roe=20.0"

    def test_asof_forward_fill_within_announcement_window(self) -> None:
        """PIT 对齐：公告后数据持续有效（forward fill），直到下一公告。"""
        from long_earn.backtest.data.financial.panel import quarterly_to_daily_asof

        quarterly = pd.DataFrame(
            {
                "symbol": ["C"] * 2,
                "report_date": pd.to_datetime(["2024-01-15", "2024-04-15"]),
                "announce_date": pd.to_datetime(["2024-02-20", "2024-05-25"]),
                "roe": [5.0, 7.0],
                "eps": [0.5, 0.7],
            }
        )

        trading_dates = pd.date_range("2024-02-20", "2024-06-30", freq="B")
        result = quarterly_to_daily_asof(
            quarterly, ["C"], trading_dates, ["roe", "eps"]
        )

        # 整个区间不应有 NaN（公告后前向填充）
        assert not result["roe"].isna().any(), "PIT 对齐后不应有缺失值"

        # 值序列：Q1 公告后 → Q2 公告后，不应有跳跃回退
        roe_vals = result["roe"].values
        for i in range(1, len(roe_vals)):
            assert roe_vals[i] >= roe_vals[i - 1], (
                f"PIT 对齐值应单调不降（roe[{i}]={roe_vals[i]} "
                f"< roe[{i - 1}]={roe_vals[i - 1]}）"
            )
