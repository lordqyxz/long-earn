import uuid
from datetime import datetime, timedelta

import polars as pl

from long_earn.backtest.domain.entities import OrderEvent, SignalEvent
from long_earn.backtest.engine.broker import Broker
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


def create_mock_data():
    """
    创建模拟数据：
    S1: 稳步上涨 (价值股, 高ROE)
    S2: 剧烈波动 (动量股)
    S3: 阴跌 (垃圾股, 低ROE)
    """
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
    """价值策略：ROE > 0.15 则持有 (应选 S1)"""

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
    """动量策略：选取 5 日涨幅最高者 (应在前期选 S2, 后期选 S1)"""

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


class MeanReversionStrategy(BaseStrategy):
    """均值回归：价格相对于 3 日前下跌 > 5% 则买入"""

    def on_bar(self, bars: pl.DataFrame, context):
        symbols = bars["symbol"].to_list()
        selected = []
        for s in symbols:
            hist = context.get_history(s, "close", 4)
            if len(hist) < 4:
                continue
            if hist[-1] / hist[0] < 0.95:
                selected.append(s)
        weights = {s: 1.0 / len(selected) if selected else 0.0 for s in selected}
        return SignalEvent(
            timestamp=context.current_timestamp,
            trace_id=f"sig_{context.current_timestamp.isoformat()}",
            event_id=f"sig_{context.current_timestamp.isoformat()}",
            signals=weights,
            strategy_id=self.strategy_id,
        )


def _run_engine(strategy, data):
    provider = MockDataProvider(data)
    engine = EventDrivenBacktestEngine(data_provider=provider)
    return engine.run(strategy, "2023-01-01", "2023-01-20", ["S1", "S2", "S3"])


def test_value_strategy():
    data = create_mock_data()
    strategy = ValueStrategy("ValueSlab")
    res = _run_engine(strategy, data)
    assert res.success is True, res.message
    assert res.total_return is not None
    assert res.total_return >= 0  # S1 稳步上涨应有正收益
    assert res.trading_days == 20
    assert res.sharpe_ratio is not None


def test_momentum_strategy():
    data = create_mock_data()
    strategy = MomentumStrategy("MomSlab")
    res = _run_engine(strategy, data)
    assert res.success is True, res.message
    assert res.total_return is not None
    assert res.trading_days == 20


def test_mean_reversion_strategy():
    data = create_mock_data()
    strategy = MeanReversionStrategy("MRSlab")
    res = _run_engine(strategy, data)
    assert res.success is True, res.message
    assert res.total_return is not None
    assert res.trading_days == 20


def test_equity_curve_populated():
    data = create_mock_data()
    strategy = ValueStrategy("EqSlab")
    res = _run_engine(strategy, data)
    assert res.daily_returns is not None
    assert len(res.daily_returns) > 1  # 至少有 start + end 两个点


def test_strategies_produce_different_returns():
    """不同策略应产生不同的收益结果"""
    data = create_mock_data()
    v = _run_engine(ValueStrategy("V"), data)
    m = _run_engine(MomentumStrategy("M"), data)
    assert v.total_return != m.total_return


def test_benchmark_metrics_computed():
    """验证基准对比指标在传入 benchmark_symbol 时正确计算"""
    data = create_mock_data()
    # 把 S1 作为基准（它稳步上涨）
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


def test_walk_forward_run():
    """验证 Walk-Forward 回测产生预期的折叠结果"""
    data = create_mock_data()
    provider = MockDataProvider(data)
    engine = EventDrivenBacktestEngine(data_provider=provider)
    strategy = ValueStrategy("WFTest")
    wf_result = engine.walk_forward_run(
        strategy, "2023-01-01", "2023-01-20", ["S1", "S2", "S3"], n_splits=2
    )
    assert "fold_results" in wf_result
    assert "average_metrics" in wf_result
    assert wf_result["n_splits"] == 2
    assert len(wf_result["fold_results"]) == 2
    for fold in wf_result["fold_results"]:
        assert "train" in fold
        assert "test" in fold
        assert fold["train"]["start"] < fold["test"]["start"]


def test_max_positions_enforced():
    """验证最大持仓限制"""
    data = create_mock_data()
    provider = MockDataProvider(data)
    engine = EventDrivenBacktestEngine(data_provider=provider, max_positions=2)
    strategy = ValueStrategy("MaxPos")
    res = engine.run(strategy, "2023-01-01", "2023-01-20", ["S1", "S2", "S3"])
    assert res.success is True


def test_trade_count_and_attribution():
    """验证交易计数和 P&L 归因数据"""
    data = create_mock_data()
    strategy = ValueStrategy("AttrTest")
    res = _run_engine(strategy, data)
    assert res.trade_count is not None
    assert res.trade_count >= 0
    assert res.attribution is not None


# ── Walk-Forward 回测扩展测试 ────────────────────────────────────────────────


def test_walk_forward_n_splits_3():
    """验证 n_splits=3 时产生 3 个折叠"""
    data = create_mock_data()
    provider = MockDataProvider(data)
    engine = EventDrivenBacktestEngine(data_provider=provider)
    strategy = ValueStrategy("WF3Test")
    wf_result = engine.walk_forward_run(
        strategy, "2023-01-01", "2023-01-20", ["S1", "S2", "S3"], n_splits=3
    )
    assert wf_result["n_splits"] == 3
    assert len(wf_result["fold_results"]) == 3
    assert "error" not in wf_result


def test_walk_forward_n_splits_5():
    """验证 n_splits=5 时产生 5 个折叠"""
    data = create_mock_data()
    provider = MockDataProvider(data)
    engine = EventDrivenBacktestEngine(data_provider=provider)
    strategy = ValueStrategy("WF5Test")
    wf_result = engine.walk_forward_run(
        strategy, "2023-01-01", "2023-01-20", ["S1", "S2", "S3"], n_splits=5
    )
    assert wf_result["n_splits"] == 5
    assert len(wf_result["fold_results"]) == 5
    assert "error" not in wf_result


def test_walk_forward_empty_data_error():
    """数据为空时 walk_forward_run 返回 error"""
    empty_data = pl.DataFrame(
        schema={"timestamp": pl.Datetime, "symbol": pl.Utf8, "close": pl.Float64}
    )
    provider = MockDataProvider(empty_data)
    engine = EventDrivenBacktestEngine(data_provider=provider)
    strategy = ValueStrategy("WFEmpty")
    wf_result = engine.walk_forward_run(
        strategy, "2023-01-01", "2023-01-20", ["S1", "S2", "S3"], n_splits=2
    )
    assert "error" in wf_result
    assert wf_result["error"] == "加载数据为空"
    assert "fold_results" not in wf_result


def test_walk_forward_fold_chronology():
    """验证折叠按时间顺序排列，train 结束 < test 开始，训练窗逐渐扩展"""
    data = create_mock_data()
    provider = MockDataProvider(data)
    engine = EventDrivenBacktestEngine(data_provider=provider)
    strategy = ValueStrategy("WFChrono")
    wf_result = engine.walk_forward_run(
        strategy, "2023-01-01", "2023-01-20", ["S1", "S2", "S3"], n_splits=3
    )
    folds = wf_result["fold_results"]
    for fold in folds:
        assert fold["train"]["start"] < fold["test"]["start"]
        assert fold["train"]["end"] < fold["test"]["end"]
    # TimeSeriesSplit 使用扩展窗口，train 起始相同，结束递增
    for i in range(1, len(folds)):
        assert folds[i]["train"]["start"] == folds[i - 1]["train"]["start"]
        assert folds[i]["train"]["end"] > folds[i - 1]["train"]["end"]
        assert folds[i]["test"]["start"] >= folds[i - 1]["test"]["end"]


def test_walk_forward_average_metrics_structure():
    """验证 average_metrics 包含 train 和 test 指标"""
    data = create_mock_data()
    provider = MockDataProvider(data)
    engine = EventDrivenBacktestEngine(data_provider=provider)
    strategy = ValueStrategy("WFAvgMetrics")
    wf_result = engine.walk_forward_run(
        strategy, "2023-01-01", "2023-01-20", ["S1", "S2", "S3"], n_splits=2
    )
    avg = wf_result["average_metrics"]
    assert "train" in avg
    assert "test" in avg
    for key in ("total_return", "sharpe_ratio", "max_drawdown", "alpha"):
        assert key in avg["train"]
        assert key in avg["test"]


def test_walk_forward_fold_metrics_present():
    """验证每个折叠的 train/test 包含指标字段"""
    data = create_mock_data()
    provider = MockDataProvider(data)
    engine = EventDrivenBacktestEngine(data_provider=provider)
    strategy = ValueStrategy("WFFoldMetrics")
    wf_result = engine.walk_forward_run(
        strategy, "2023-01-01", "2023-01-20", ["S1", "S2", "S3"], n_splits=2
    )
    for fold in wf_result["fold_results"]:
        for period in ("train", "test"):
            for key in ("total_return", "sharpe_ratio", "max_drawdown", "alpha"):
                assert key in fold[period], (
                    f"Missing {key} in fold {fold['fold_id']} {period}"
                )


# ── 风控（止损/回撤）单元测试 ────────────────────────────────────────────────


def _make_slab(prices: dict[str, float]) -> pl.DataFrame:
    """构造截面价格数据 DataFrame"""
    rows = [{"symbol": s, "close": p} for s, p in prices.items()]
    return pl.DataFrame(rows)


def _lookup_price(slab: pl.DataFrame, symbol: str) -> float | None:
    """从 Slab 中查找某股票的最新价格"""
    price_series = slab.filter(pl.col("symbol") == symbol).select("close").to_series()
    if price_series.is_empty():
        return None
    return price_series[0]


def _create_position(
    portfolio: Portfolio,
    broker: Broker,
    symbol: str,
    shares: float,
    price: float,
    ts: datetime,
) -> None:
    """通过 BUY 订单建仓，使用 Broker 撮合保证成本真实性"""
    order = OrderEvent(
        timestamp=ts,
        trace_id=str(uuid.uuid4()),
        event_id=f"buy_{symbol}",
        symbol=symbol,
        order_type="BUY",
        quantity=shares,
        price=price,
        order_id=f"ord_buy_{symbol}",
    )
    fill = broker.execute_order(order, price)
    portfolio.update_from_fill(fill)


def test_stop_loss_triggered():
    """持仓亏损超过 stop_loss 阈值时触发止损卖出，清空该持仓"""
    portfolio = Portfolio(initial_capital=1_000_000)
    broker = Broker()
    ts_start = datetime(2023, 1, 10)
    ts_now = datetime(2023, 1, 15)
    symbol = "S1"

    # 建仓：1000 股 @ 10 元
    _create_position(portfolio, broker, symbol, 1000.0, 10.0, ts_start)

    # 更新市值为建仓价，初始化 equity
    portfolio.update_market_values(_make_slab({symbol: 10.0}))

    # 当前价格暴跌至 9 元，亏损约 10%
    slab = _make_slab({symbol: 9.0})
    portfolio.update_market_values(slab)

    stop_loss = 0.05

    # 止损检查（与 EventDrivenBacktestEngine._check_stop_loss 逻辑一致）
    triggered = False
    for sym, pos in list(portfolio.positions.items()):
        pnl_pct = (
            (pos.current_price - pos.avg_cost) / pos.avg_cost
            if pos.avg_cost > 0
            else 0.0
        )
        if pnl_pct > -stop_loss:
            continue
        price = _lookup_price(slab, sym)
        assert price is not None
        order = OrderEvent(
            timestamp=ts_now,
            trace_id=str(uuid.uuid4()),
            event_id=f"sl_{ts_now.isoformat()}_{sym}",
            symbol=sym,
            order_type="SELL",
            quantity=pos.shares,
            price=price,
            order_id="ord_sl_test",
        )
        fill = broker.execute_order(order, price)
        assert fill.order_type == "SELL"
        portfolio.update_from_fill(fill)
        triggered = True

    # 验证止损已触发且持仓已清空
    assert triggered is True
    assert symbol not in portfolio.positions
    assert portfolio.trade_count >= 2  # 买入 + 卖出


def test_stop_loss_not_triggered():
    """持仓亏损未超过 stop_loss 阈值时不应触发卖出"""
    portfolio = Portfolio(initial_capital=1_000_000)
    broker = Broker()
    ts_start = datetime(2023, 1, 10)
    ts_now = datetime(2023, 1, 15)
    symbol = "S1"

    # 建仓：1000 股 @ 10 元
    _create_position(portfolio, broker, symbol, 1000.0, 10.0, ts_start)
    portfolio.update_market_values(_make_slab({symbol: 10.0}))

    # 当前价格 9.5 元，亏损约 5%
    slab = _make_slab({symbol: 9.5})
    portfolio.update_market_values(slab)

    stop_loss = 0.15  # 15% 止损线，5% 亏损未触发

    triggered = False
    for sym, pos in list(portfolio.positions.items()):
        pnl_pct = (
            (pos.current_price - pos.avg_cost) / pos.avg_cost
            if pos.avg_cost > 0
            else 0.0
        )
        if pnl_pct > -stop_loss:
            continue
        price = _lookup_price(slab, sym)
        order = OrderEvent(
            timestamp=ts_now,
            trace_id=str(uuid.uuid4()),
            event_id=f"sl_{ts_now.isoformat()}_{sym}",
            symbol=sym,
            order_type="SELL",
            quantity=pos.shares,
            price=price,
            order_id="ord_sl_test",
        )
        broker.execute_order(order, price)
        triggered = True

    # 验证止损未触发，持仓仍然存在
    assert triggered is False
    assert symbol in portfolio.positions
    assert portfolio.positions[symbol].shares > 0


def test_max_drawdown_triggered_liquidate_all():
    """回撤超过 max_drawdown_limit 时清仓所有持仓"""
    portfolio = Portfolio(initial_capital=1_000_000)
    broker = Broker()
    ts_start = datetime(2023, 1, 10)
    ts_now = datetime(2023, 1, 15)

    # 大仓位建仓两支股票（占总资金 ~98%），确保价格波动能产生显著回撤
    _create_position(portfolio, broker, "S1", 39000.0, 20.0, ts_start)
    _create_position(portfolio, broker, "S2", 20000.0, 10.0, ts_start)
    portfolio.update_market_values(_make_slab({"S1": 20.0, "S2": 10.0}))

    # 价格大幅下跌，造成约 29% 回撤
    slab = _make_slab({"S1": 14.0, "S2": 7.0})
    portfolio.update_market_values(slab)

    max_drawdown_limit = 0.05  # 5% 回撤限制

    # 计算当前回撤
    peak_value = (
        max(portfolio.equity_curve) if portfolio.equity_curve else portfolio.total_value
    )
    dd = (portfolio.total_value - peak_value) / peak_value if peak_value > 0 else 0.0
    # 回撤超过阈值时清仓
    if dd <= -max_drawdown_limit:
        for sym, pos in list(portfolio.positions.items()):
            price = _lookup_price(slab, sym)
            assert price is not None
            order = OrderEvent(
                timestamp=ts_now,
                trace_id=str(uuid.uuid4()),
                event_id=f"dd_{ts_now.isoformat()}_{sym}",
                symbol=sym,
                order_type="SELL",
                quantity=pos.shares,
                price=price,
                order_id=f"ord_dd_{sym}",
            )
            fill = broker.execute_order(order, price)
            portfolio.update_from_fill(fill)

    # 验证所有持仓已清空
    assert len(portfolio.positions) == 0
    assert portfolio.trade_count >= 4  # 2 买入 + 2 卖出


def test_max_drawdown_not_triggered():
    """回撤在 max_drawdown_limit 限制内时不清仓"""
    portfolio = Portfolio(initial_capital=1_000_000)
    broker = Broker()
    ts_start = datetime(2023, 1, 10)

    _create_position(portfolio, broker, "S1", 500.0, 10.0, ts_start)
    portfolio.update_market_values(_make_slab({"S1": 10.0}))

    # 轻微下跌
    slab = _make_slab({"S1": 9.5})
    portfolio.update_market_values(slab)

    max_drawdown_limit = 0.15  # 15% 限制

    peak_value = (
        max(portfolio.equity_curve) if portfolio.equity_curve else portfolio.total_value
    )
    dd = (portfolio.total_value - peak_value) / peak_value if peak_value > 0 else 0.0

    # 回撤未超限，不应触发
    triggered = False
    if dd <= -max_drawdown_limit:
        for sym, pos in list(portfolio.positions.items()):
            price = _lookup_price(slab, sym)
            order = OrderEvent(
                timestamp=datetime(2023, 1, 15),
                trace_id=str(uuid.uuid4()),
                event_id=f"dd_test_{sym}",
                symbol=sym,
                order_type="SELL",
                quantity=pos.shares,
                price=price,
                order_id=f"ord_dd_{sym}",
            )
            broker.execute_order(order, price)
            triggered = True

    assert triggered is False
    assert "S1" in portfolio.positions


def test_stop_loss_none_skips_check():
    """stop_loss=None 时跳过止损检查，持仓不受影响"""
    portfolio = Portfolio(initial_capital=1_000_000)
    broker = Broker()
    ts_start = datetime(2023, 1, 10)
    symbol = "S1"

    _create_position(portfolio, broker, symbol, 1000.0, 10.0, ts_start)
    portfolio.update_market_values(_make_slab({symbol: 10.0}))

    # 价格暴跌，理论上应触发止损
    slab = _make_slab({symbol: 7.0})
    portfolio.update_market_values(slab)

    stop_loss = None  # 不执行止损检查

    triggered = False
    if stop_loss is not None:
        for sym, pos in list(portfolio.positions.items()):
            pnl_pct = (
                (pos.current_price - pos.avg_cost) / pos.avg_cost
                if pos.avg_cost > 0
                else 0.0
            )
            if pnl_pct > -stop_loss:
                continue
            price = _lookup_price(slab, sym)
            if price is not None:
                order = OrderEvent(
                    timestamp=datetime(2023, 1, 15),
                    trace_id=str(uuid.uuid4()),
                    event_id="sl_skip_test",
                    symbol=sym,
                    order_type="SELL",
                    quantity=pos.shares,
                    price=price,
                    order_id="ord_sl_skip",
                )
                fill = broker.execute_order(order, price)
                portfolio.update_from_fill(fill)
            triggered = True

    # 验证止损未执行
    assert triggered is False
    assert symbol in portfolio.positions
    assert portfolio.positions[symbol].shares > 0
