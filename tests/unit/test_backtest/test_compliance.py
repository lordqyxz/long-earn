"""A 股合规专项测试（AUDIT-P1-14）

覆盖已实现的合规约束：
  - T+1 制度（P0-06）：当日买入不可当日卖出
  - 涨跌停板（P0-07）：涨停不可买入、跌停不可卖出
  - 成交量限制（P0-04）：部分成交 + 冲击成本
  - 过户费（P1-03）：沪市双向万分之 0.1
  - 止盈（P1-05）：盈利超阈值强制卖出
  - 停牌（P1-09）：零成交量拒绝交易

每个测试独立构造引擎+策略，不依赖真实数据源。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl

from long_earn.backtest.engine.broker import Broker, TradingCostConfig
from long_earn.backtest.engine.core import EventDrivenBacktestEngine


def _trending_panel(
    days: int = 10,
    symbols: list[str] | None = None,
    start: datetime | None = None,
) -> pl.DataFrame:
    """构造确定性上涨面板供测试使用。"""
    rows = []
    base = start or datetime(2024, 1, 1)
    syms = symbols or ["A.SZ"]
    for i in range(days):
        ts = base + timedelta(days=i)
        for sym in syms:
            close = round(10.0 * (1.005**i), 4)
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": sym,
                    "open": close * 0.99,
                    "high": close * 1.01,
                    "low": close * 0.98,
                    "close": close,
                    "volume": 100000.0,
                }
            )
    return pl.DataFrame(rows)


class _SimpleBuyStrategy:
    """简单的买入持有策略。"""

    def __init__(self, strategy_id: str = "test"):
        self.strategy_id = strategy_id
        self._state: dict = {}
        self._called = False

    def init(self) -> None:
        self._called = False

    def on_bar(self, bars: pl.DataFrame, context=None):
        from long_earn.backtest.domain.entities import SignalEvent

        if not self._called:
            self._called = True
            return SignalEvent(
                timestamp=bars["timestamp"][0],
                trace_id="trace-buy",
                event_id="sig-buy",
                signals={"A.SZ": 1.0},
                strategy_id=self.strategy_id,
            )
        return None


class _T1SellStrategy:
    """T 日买入、T+2 日卖出策略，用于验证 T+1 约束。

    由于引擎的 T+1 延迟执行（T 日信号在 T+1 日以 open 价成交），
    买入在 T+1 日成交，available_date = T+1+1day = T+2；
    T+2 日产生卖出信号、T+3 日执行，此时 ts >= available_date 恒成立。
    故本策略用于"卖出被允许"的正向用例。
    """

    def __init__(self):
        self._state: dict = {}
        self._step = 0

    def init(self) -> None:
        self._step = 0

    def on_bar(self, bars: pl.DataFrame, context=None):
        from long_earn.backtest.domain.entities import SignalEvent

        self._step += 1
        if self._step == 1:
            # T 日买入
            return SignalEvent(
                timestamp=bars["timestamp"][0],
                trace_id="trace-buy",
                event_id="sig-buy",
                signals={"A.SZ": 1.0},
                strategy_id="t1_test",
            )
        if self._step >= 3:
            # T+2 日卖出（T+1 日应被锁定）
            return SignalEvent(
                timestamp=bars["timestamp"][0],
                trace_id="trace-sell",
                event_id="sig-sell",
                signals={"A.SZ": 0.0},
                strategy_id="t1_test",
            )
        return None


class _T1SameDaySellStrategy:
    """买入后持续尝试卖出的策略，用于验证 T+1 锁定对止损风控的阻塞。

    由于引擎采用 T+1 延迟执行架构（T 日信号在 T+1 日以 open 成交），
    常规信号路径下卖出总是满足 ts >= available_date（available_date=成交日+1，
    信号在下一日执行 ts=下一日 ≥ available_date）。
    T+1 锁定分支（portfolio.py:247-260）主要保护**风控同步卖出**路径：
    止损/止盈/最大回撤在 ts 时同步执行，若 ts < available_date 则跳过，
    防止当日买入当日被风控卖出。

    本策略配合暴跌面板 + 严格止损：T 日买入，T+1 日成交（available_date=T+2），
    T+1 日暴跌触发止损检查，但 ts=T+1 < available_date=T+2 → 止损卖出被 T+1 锁定跳过。
    """

    def __init__(self):
        self._state: dict = {}
        self._step = 0

    def init(self) -> None:
        self._step = 0

    def on_bar(self, bars: pl.DataFrame, context=None):
        from long_earn.backtest.domain.entities import SignalEvent

        self._step += 1
        ts = bars["timestamp"][0]
        if self._step == 1:
            return SignalEvent(
                timestamp=ts,
                trace_id="trace-buy",
                event_id="sig-buy",
                signals={"A.SZ": 1.0},
                strategy_id="t1_block_test",
            )
        return None


# ── T+1 制度（P0-06）─────────────────────────────────────────────


def test_t1_portfolio_locks_sell_before_available_date():
    """T+1：Portfolio._compute_order_infos 在 ts < available_date 时跳过卖出。

    白盒测试 T+1 锁定分支（portfolio.py:247-260）——属于关键风控路径
    （AGENTS.md 测试原则：系统关键环节必须测试），非实现细节测试。
    验证 skip_reason = OrderSkipReason.T1_LOCKED 且不生成卖出订单。
    """
    from long_earn.backtest.domain.entities import FillEvent
    from long_earn.backtest.engine.audit import OrderSkipReason
    from long_earn.backtest.engine.portfolio import Portfolio

    portfolio = Portfolio()
    # 模拟 T+1 日（01-02）买入成交，available_date = 01-03
    fill = FillEvent(
        timestamp=datetime(2024, 1, 2),
        trace_id="f",
        event_id="f",
        order_id="o1",
        symbol="A.SZ",
        order_type="BUY",
        fill_price=10.0,
        fill_quantity=1000.0,
        commission=5.0,
        slippage=0.0,
        stamp_duty=0.0,
        transfer_fee=1.0,
    )
    portfolio.update_from_fill(fill)
    # 更新市值，使 current_val > 0（否则 diff_val=0 会在 T+1 检查前被 < 1.0 过滤）
    slab = pl.DataFrame(
        [
            {
                "timestamp": datetime(2024, 1, 2),
                "symbol": "A.SZ",
                "close": 10.0,
                "open": 10.0,
            }
        ]
    )
    portfolio.update_market_values(slab)
    assert portfolio.positions["A.SZ"].available_date == datetime(2024, 1, 3)

    # 在 available_date 之前（01-02）尝试卖出 → 应被 T+1 锁定跳过
    prices = pl.DataFrame(
        [{"timestamp": datetime(2024, 1, 2), "symbol": "A.SZ", "open": 10.0}]
    )
    infos = portfolio._compute_order_infos(
        {"A.SZ": 0.0}, prices, max_position_pct=1.0, price_field="open"
    )
    skipped = [i for i in infos if i.get("skipped")]
    assert len(skipped) >= 1, "ts < available_date 时卖出应被标记 skipped"
    assert skipped[0]["skip_reason"] == OrderSkipReason.T1_LOCKED, (
        f"skip_reason 应为 T1_LOCKED，实际: {skipped[0]['skip_reason']}"
    )

    # 在 available_date 当天（01-03）尝试卖出 → 应正常生成卖出订单
    prices2 = pl.DataFrame(
        [{"timestamp": datetime(2024, 1, 3), "symbol": "A.SZ", "open": 10.0}]
    )
    infos2 = portfolio._compute_order_infos(
        {"A.SZ": 0.0}, prices2, max_position_pct=1.0, price_field="open"
    )
    normal_sells = [
        i for i in infos2 if not i.get("skipped") and i["order_type"] == "SELL"
    ]
    assert len(normal_sells) >= 1, "ts >= available_date 时卖出应正常生成订单"


def test_t1_blocks_same_day_sell():
    """T+1：当日买入的股票当日不可被风控卖出（引擎级集成验证）。

    构造场景：T 日（day0）买入信号，T+1 日（day1）以 open=10 成交
    （available_date=day2），day1 close=4（暴跌 60%）触发止损检查，
    但 ts=day1 < available_date=day2，止损卖出应被 T+1 锁定跳过。

    关键：open=10（成交价高）而 close/low=4/3.9（暴跌），
    使 avg_cost≈10 而 pnl_pct≈-60% < -5% 止损线，止损必然触发但被 T+1 阻止。
    """
    rows = []
    base = datetime(2024, 1, 1)
    # day0: close=10（信号产生日）；day1: open=10 成交，close=4 暴跌；后续平 4
    for i in range(8):
        ts = base + timedelta(days=i)
        if i == 0:
            open_p = close_p = 10.0
        elif i == 1:
            open_p = 10.0  # T+1 成交价（高）
            close_p = 4.0  # 当日暴跌 60%
        else:
            open_p = close_p = 4.0
        rows.append(
            {
                "timestamp": ts,
                "symbol": "A.SZ",
                "open": open_p,
                "high": close_p * 1.01,
                "low": close_p * 0.98,
                "close": close_p,
                "volume": 100000.0,
            }
        )
    panel = pl.DataFrame(rows)

    class _MockProvider:
        def get_merged_panel_as_polars(self, *args, **kwargs):
            return panel

    engine = EventDrivenBacktestEngine(data_provider=_MockProvider(), stop_loss=0.05)
    strategy = _T1SameDaySellStrategy()

    result = engine.run(strategy, "2024-01-01", "2024-01-10", ["A.SZ"])
    assert result.success, f"回测失败: {result.message}"

    # T+1 锁定：day1 止损触发但被跳过（ts=day1 < available_date=day2）
    # day2 止损才执行（清仓），故 trade_count=2（买入+止损卖出）
    # 但若 T+1 锁定失效，day1 就会卖出 → trade_count 仍为 2，无法区分。
    # 因此用更精确的断言：day1 的审计 trail 中不应有 FILL(SELL)，
    # 且 day1 有 stop_loss 的 RISK_TRIGGER 被跳过的痕迹。
    trail = engine.audit_logger.get_full_trail()
    # 关键：止损在 day1 被跳过（T+1 锁定，ts=day1 < available_date=day2），
    # day2 才执行卖出。验证止损最终触发并卖出。
    sl_triggers = [
        e
        for e in trail
        if e.get("event_type") == "RISK_TRIGGER"
        and e.get("payload", {}).get("risk_type") == "stop_loss"
    ]
    # 止损最终在 day2 触发并卖出
    assert len(sl_triggers) >= 1, "止损应在 available_date 之后触发"
    # 至少有买入 + 止损卖出
    assert result.trade_count >= 2, (
        f"应有买入+止损卖出（trade_count>=2），实际 {result.trade_count}"
    )
    # P2-NEW-2 加强：T+1 锁定的核心效果是止损被延迟到 day2（2024-01-03）。
    # 若 T+1 锁定失效（回归），止损会在 day1（2024-01-02）成交。
    # 断言 day1 无 stop_loss RISK_TRIGGER，能检测 T+1 锁定对风控路径的回归。
    day1_triggers = [
        t
        for t in sl_triggers
        if "2024-01-02" in t.get("payload", {}).get("timestamp", "")
    ]
    assert len(day1_triggers) == 0, (
        "day1(2024-01-02) 不应有 stop_loss RISK_TRIGGER（T+1 锁定应阻止当日卖出）；"
        "若出现则 T+1 锁定对风控路径失效"
    )


def test_t1_allows_next_day_sell(mock_data_provider):
    """T+1：T+1 日卖出不触发停牌（T+1 日 open 价执行后，持仓已到账）。

    加强断言：卖出应真正成交（trade_count >= 2，含买入+卖出），
    且无 T+1 锁定的 ORDER_SKIPPED 事件。
    """
    panel = _trending_panel(days=5)
    provider = mock_data_provider(panel)
    engine = EventDrivenBacktestEngine(data_provider=provider)
    strategy = _T1SellStrategy()

    result = engine.run(strategy, "2024-01-01", "2024-01-07", ["A.SZ"])
    assert result.success
    # 买入 + 卖出两笔成交
    assert result.trade_count is not None and result.trade_count >= 2, (
        f"T+1 后卖出应真正成交，trade_count 应 >= 2，实际 {result.trade_count}"
    )
    # 不应有 T+1 锁定跳过
    trail = engine.audit_logger.get_full_trail()
    t1_skips = [
        e
        for e in trail
        if e.get("event_type") == "ORDER_SKIPPED"
        and "T+1" in e.get("payload", {}).get("reason", "")
    ]
    assert len(t1_skips) == 0, (
        f"合规卖出不应触发 T+1 锁定，但仍出现 ORDER_SKIPPED(T+1): {t1_skips}"
    )


# ── 涨跌停板（P0-07）─────────────────────────────────────────────


def _limit_panel(days: int = 10) -> pl.DataFrame:
    """构造涨停板场景：第 3 天 close 相比第 2 天涨幅超过 10%。"""
    rows = []
    base = datetime(2024, 1, 1)
    for i in range(days):
        ts = base + timedelta(days=i)
        if i <= 1:
            close = 10.0
        elif i == 2:
            close = 11.5  # 第 3 天涨幅 15% > 10%
        else:
            close = 11.5
        rows.append(
            {
                "timestamp": ts,
                "symbol": "A.SZ",
                "open": close * 0.99,
                "high": close * 1.01,
                "low": close * 0.98,
                "close": close,
                "volume": 100000.0,
            }
        )
    return pl.DataFrame(rows)


def test_limit_up_blocks_buy():
    """涨停板：涨停日买入订单被 ORDER_SKIPPED。

    直接测试 _check_limit_up_down 逻辑，绕过 T+1 延迟影响。
    """
    from long_earn.backtest.engine.audit import OrderSkipReason

    engine = EventDrivenBacktestEngine(data_provider=None)
    # 手动设置涨跌停限价
    engine._current_limit_up_map["A.SZ"] = 10.5
    engine._current_limit_down_map["A.SZ"] = 8.5

    # 价格 11.0 >= 涨停价 10.5，应拒绝
    result = engine._check_limit_up_down("A.SZ", "BUY", 11.0)
    assert result is not None, "涨停价之上买入应被拒绝"
    assert result[0] == OrderSkipReason.LIMIT_UP_REJECT, (
        f"拒绝原因应为 LIMIT_UP_REJECT: {result}"
    )

    # 价格 10.0 < 涨停价 10.5，应通过
    result2 = engine._check_limit_up_down("A.SZ", "BUY", 10.0)
    assert result2 is None, "涨停价之下买入应通过"


def test_limit_down_blocks_sell():
    """跌停板：跌停日卖出订单被 ORDER_SKIPPED。"""
    from long_earn.backtest.engine.audit import OrderSkipReason

    engine = EventDrivenBacktestEngine(data_provider=None)
    engine._current_limit_up_map["A.SZ"] = 11.0
    engine._current_limit_down_map["A.SZ"] = 9.0

    # 价格 8.5 <= 跌停价 9.0，应拒绝
    result = engine._check_limit_up_down("A.SZ", "SELL", 8.5)
    assert result is not None, "跌停价之下卖出应被拒绝"
    assert result[0] == OrderSkipReason.LIMIT_DOWN_REJECT, (
        f"拒绝原因应为 LIMIT_DOWN_REJECT: {result}"
    )

    # 价格 9.5 > 跌停价 9.0，应通过
    result2 = engine._check_limit_up_down("A.SZ", "SELL", 9.5)
    assert result2 is None, "跌停价之上卖出应通过"


def test_compute_price_limits_formula():
    """_compute_price_limits 应满足 round(prev_close*1.1, 2) / round(prev_close*0.9, 2)。"""
    up, down = EventDrivenBacktestEngine._compute_price_limits(10.0)
    assert up == 11.0
    assert down == 9.0
    # 非正前收盘价应返回 inf / 0（防御）
    up0, down0 = EventDrivenBacktestEngine._compute_price_limits(0.0)
    assert up0 == float("inf") and down0 == 0.0


def test_limit_up_blocks_buy_integration(mock_data_provider):
    """涨停板集成测试：引擎级 _process_timestamp 基于 _prev_close_map 动态计算涨跌停，
    涨停日买入订单应被 ORDER_SKIPPED（reason=OrderSkipReason.LIMIT_UP_REJECT）。

    构造场景：第 0-1 日 close=10（_prev_close_map 建立），
    第 2 日 close=11.5（涨幅 15% > 10%），open=11.5*0.99=11.385 >= 涨停价 11.0，
    买入订单应被拒。
    """
    panel = _limit_panel(days=5)
    provider = mock_data_provider(panel)
    engine = EventDrivenBacktestEngine(data_provider=provider)

    # 策略：每个 bar 都发出买入信号（待 T+1 执行）
    class _AlwaysBuy:
        def __init__(self):
            self._state: dict = {}
            self.strategy_id = "limit_buy_test"

        def init(self):
            self._state = {}

        def on_bar(self, bars, context=None):
            from long_earn.backtest.domain.entities import SignalEvent

            return SignalEvent(
                timestamp=bars["timestamp"][0],
                trace_id="trace-limit-buy",
                event_id="sig-limit-buy",
                signals={"A.SZ": 1.0},
                strategy_id="limit_buy_test",
            )

    result = engine.run(_AlwaysBuy(), "2024-01-01", "2024-01-07", ["A.SZ"])
    assert result.success

    # 至少存在一个涨停拒买的 ORDER_SKIPPED
    from long_earn.backtest.engine.audit import OrderSkipReason

    trail = engine.audit_logger.get_full_trail()
    limit_skips = [
        e
        for e in trail
        if e.get("event_type") == "ORDER_SKIPPED"
        and e.get("payload", {}).get("reason") == OrderSkipReason.LIMIT_UP_REJECT
    ]
    assert len(limit_skips) >= 1, (
        "涨停日买入应被 ORDER_SKIPPED（reason=LIMIT_UP_REJECT），"
        f"实际 ORDER_SKIPPED: "
        f"{[e.get('payload', {}).get('reason') for e in trail if e.get('event_type') == 'ORDER_SKIPPED']}"
    )


# ── 停牌（P1-09）─────────────────────────────────────────────────


def test_suspend_zero_volume_blocks_trade(mock_data_provider):
    """停牌：当日成交量为 0 时订单应被 ORDER_SKIPPED（reason=OrderSkipReason.SUSPENDED）。

    _pre_trade_check 在 volume==0 时返回 SUSPENDED。
    """
    # 构造面板：第 2 日 volume=0（停牌），其余正常
    rows = []
    base = datetime(2024, 1, 1)
    for i in range(5):
        ts = base + timedelta(days=i)
        close = 10.0
        vol = 0.0 if i == 1 else 100000.0
        rows.append(
            {
                "timestamp": ts,
                "symbol": "A.SZ",
                "open": close * 0.99,
                "high": close * 1.01,
                "low": close * 0.98,
                "close": close,
                "volume": vol,
            }
        )
    panel = pl.DataFrame(rows)
    provider = mock_data_provider(panel)
    engine = EventDrivenBacktestEngine(data_provider=provider)

    class _AlwaysBuy:
        def __init__(self):
            self._state: dict = {}
            self.strategy_id = "suspend_test"

        def init(self):
            self._state = {}

        def on_bar(self, bars, context=None):
            from long_earn.backtest.domain.entities import SignalEvent

            return SignalEvent(
                timestamp=bars["timestamp"][0],
                trace_id="trace-suspend",
                event_id="sig-suspend",
                signals={"A.SZ": 1.0},
                strategy_id="suspend_test",
            )

    result = engine.run(_AlwaysBuy(), "2024-01-01", "2024-01-07", ["A.SZ"])
    assert result.success

    from long_earn.backtest.engine.audit import OrderSkipReason

    trail = engine.audit_logger.get_full_trail()
    suspend_skips = [
        e
        for e in trail
        if e.get("event_type") == "ORDER_SKIPPED"
        and e.get("payload", {}).get("reason") == OrderSkipReason.SUSPENDED
    ]
    assert len(suspend_skips) >= 1, (
        "停牌日（volume=0）订单应被 ORDER_SKIPPED（reason=SUSPENDED），"
        f"实际 ORDER_SKIPPED: "
        f"{[e.get('payload', {}).get('reason') for e in trail if e.get('event_type') == 'ORDER_SKIPPED']}"
    )


# ── 过户费（P1-03）─────────────────────────────────────────────


def test_transfer_fee_sh_only():
    """过户费：沪市 (.SH) 双向征收，深市 (.SZ) 不收。"""
    cost = TradingCostConfig(transfer_fee_rate=0.00001)
    broker = Broker(cost_config=cost)

    from long_earn.backtest.domain.entities import OrderEvent

    # 沪市买入
    order_sh = OrderEvent(
        timestamp=datetime(2024, 1, 1),
        trace_id="t1",
        event_id="e1",
        symbol="600519.SH",
        order_type="BUY",
        quantity=1000.0,
        price=100.0,
    )
    fill_sh = broker.execute_order(order_sh, 100.0)
    assert fill_sh.transfer_fee > 0, "沪市买入应征收过户费"

    # 深市买入
    order_sz = OrderEvent(
        timestamp=datetime(2024, 1, 1),
        trace_id="t2",
        event_id="e2",
        symbol="000001.SZ",
        order_type="BUY",
        quantity=1000.0,
        price=100.0,
    )
    fill_sz = broker.execute_order(order_sz, 100.0)
    assert fill_sz.transfer_fee == 0, "深市不应征收过户费"


def test_transfer_fee_both_sides():
    """过户费：买入和卖出都征收（沪市）。"""
    cost = TradingCostConfig(transfer_fee_rate=0.00001)
    broker = Broker(cost_config=cost)

    from long_earn.backtest.domain.entities import OrderEvent

    order = OrderEvent(
        timestamp=datetime(2024, 1, 1),
        trace_id="t1",
        event_id="e1",
        symbol="600519.SH",
        order_type="SELL",
        quantity=1000.0,
        price=100.0,
    )
    fill = broker.execute_order(order, 100.0)
    assert fill.transfer_fee > 0, "沪市卖出也应征收过户费"


# ── 成交量限制（P0-04）─────────────────────────────────────────


def test_volume_participation_limit(mock_data_provider):
    """成交量限制：大单应被限制为日成交量的 5%。"""
    panel = _trending_panel(days=5)
    provider = mock_data_provider(panel)

    # 设置低成交量参与率 5%
    cost = TradingCostConfig(max_volume_participation=0.05)
    engine = EventDrivenBacktestEngine(data_provider=provider, cost_config=cost)

    class _LargeOrder:
        def __init__(self):
            self._state: dict = {}
            self._called = False
            self.strategy_id = "large_test"

        def init(self):
            self._called = False

        def on_bar(self, bars, context=None):
            from long_earn.backtest.domain.entities import SignalEvent

            if not self._called:
                self._called = True
                return SignalEvent(
                    timestamp=bars["timestamp"][0],
                    trace_id="trace-large",
                    event_id="sig-large",
                    signals={"A.SZ": 1.0},
                    strategy_id="large_test",
                )
            return None

    result = engine.run(_LargeOrder(), "2024-01-01", "2024-01-07", ["A.SZ"])
    assert result.success

    # 验证成交量限制后的部分成交记录
    trail = engine.audit_logger.get_full_trail()
    fills = [e for e in trail if e.get("event_type") == "FILL"]
    assert len(fills) >= 1, "至少有一次成交"

    # 大资金(100万) * 100% 仓位买入，volume=100000, price≈10,
    # max_qty = 100000 * 0.05 = 5000, order_qty = 1000000/10 = 100000
    # fill_qty = min(100000, 5000) = 5000 → partial_fill=True
    partial_fills = [f for f in fills if f.get("payload", {}).get("partial_fill")]
    assert len(partial_fills) >= 1, "大单应被部分成交"


# ── 止盈（P1-05）─────────────────────────────────────────────


def test_take_profit_triggers_sell(mock_data_provider):
    """止盈：持仓盈利超过阈值时强制卖出。"""
    panel = _trending_panel(days=15)
    provider = mock_data_provider(panel)

    engine = EventDrivenBacktestEngine(
        data_provider=provider,
        take_profit=0.02,  # 2% 止盈
    )

    class _BuyHold:
        def __init__(self):
            self._state: dict = {}
            self._called = False
            self.strategy_id = "tp_test"

        def init(self):
            self._called = False

        def on_bar(self, bars, context=None):
            from long_earn.backtest.domain.entities import SignalEvent

            if not self._called:
                self._called = True
                return SignalEvent(
                    timestamp=bars["timestamp"][0],
                    trace_id="trace-tp",
                    event_id="sig-tp",
                    signals={"A.SZ": 1.0},
                    strategy_id="tp_test",
                )
            return None

    result = engine.run(_BuyHold(), "2024-01-01", "2024-01-17", ["A.SZ"])
    assert result.success

    # 止盈触发 → 应有 RISK_TRIGGER 事件
    trail = engine.audit_logger.get_full_trail()
    tp_triggers = [
        e
        for e in trail
        if e.get("event_type") == "RISK_TRIGGER"
        and e.get("payload", {}).get("risk_type") == "take_profit"
    ]
    assert len(tp_triggers) >= 1, "上涨趋势中止盈应触发"
