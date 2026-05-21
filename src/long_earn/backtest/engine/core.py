"""事件驱动回测引擎核心

实现 T 维度迭代 × S 维度向量化 (Slab) 的执行链路。
"""

import logging
import uuid
from typing import Any

import numpy as np
import polars as pl

from long_earn.backtest.domain.entities import (
    MarketDataEvent,
    OrderEvent,
    PerformanceMetrics,
)
from long_earn.backtest.engine.broker import Broker, TradingCostConfig
from long_earn.backtest.engine.ml_strategy import TimeSeriesSplit
from long_earn.backtest.engine.portfolio import Portfolio
from long_earn.backtest.engine.strategy import BaseStrategy
from long_earn.backtest.engine.visibility import VisibilityGuard
from long_earn.backtest.models import BacktestResult

logger = logging.getLogger(__name__)


def _empty_bm() -> dict[str, float]:
    return {
        "alpha": 0.0,
        "beta": 0.0,
        "information_ratio": 0.0,
        "tracking_error": 0.0,
        "benchmark_return": 0.0,
    }


class InMemoryAuditTrail:
    """内存审计跟踪，用于测试和快速查询因果链"""

    def __init__(self):
        self.trail: list[dict[str, Any]] = []

    def log_transition(self, **kwargs) -> None:
        self.trail.append(kwargs)

    def get_full_trail(self) -> list[dict[str, Any]]:
        return self.trail


class EventDrivenBacktestEngine:
    """
    事件驱动回测引擎

    执行流程：
    T-Loop → MarketDataEvent → Strategy.on_bar → SignalEvent → Portfolio → OrderEvent → Broker → FillEvent → Portfolio.update
    """

    MIN_TRADING_DAYS = 2
    MIN_BM_POINTS = 2

    def __init__(  # noqa: PLR0913
        self,
        data_provider: Any = None,
        universe_provider: Any = None,
        cost_config: TradingCostConfig | None = None,
        audit_provider: Any = None,
        stop_loss: float | None = None,
        max_drawdown_limit: float | None = None,
        max_position_pct: float = 1.0,
        max_positions: int = 0,
    ):
        self.data_provider = data_provider
        self.universe_provider = universe_provider
        self.cost_config = cost_config or TradingCostConfig()
        self.audit_provider = audit_provider
        self.audit_logger = InMemoryAuditTrail()
        self.stop_loss = stop_loss
        self.max_drawdown_limit = max_drawdown_limit
        self.max_position_pct = max_position_pct
        self.max_positions = max_positions

    # ── 主入口 ────────────────────────────────────────────────

    def run(
        self,
        strategy: BaseStrategy,
        start_date: str,
        end_date: str,
        symbols: list[str],
        benchmark_symbol: str = "",
    ) -> BacktestResult:
        """执行回测

        Args:
            strategy: 策略实例
            start_date: 起始日期
            end_date: 结束日期
            symbols: 候选股票列表
            benchmark_symbol: 基准指数代码（如 "000300"），用于计算 Alpha/Beta 等
        """
        try:
            full_data = self._prepare_data(symbols, start_date, end_date)
            if full_data.is_empty():
                return BacktestResult(success=False, message="加载数据为空")

            guard = VisibilityGuard(full_data)
            portfolio = Portfolio()
            broker = Broker(self.cost_config)
            broker.reset()
            strategy.init()

            run_id = str(uuid.uuid4())
            self.audit_logger.trail.clear()
            db_audit = self._init_db_audit(run_id)

            timestamps = self._get_timestamps(full_data)

            for bar_idx, ts in enumerate(timestamps):
                self._process_timestamp(
                    ts, guard, portfolio, broker, strategy, db_audit, bar_idx
                )

            self._finalize_mark_to_market(portfolio, full_data, timestamps[-1])
            return self._build_result(
                portfolio,
                len(timestamps),
                full_data,
                benchmark_symbol,
            )

        except Exception as e:
            logger.exception("回测引擎执行失败")
            return BacktestResult(success=False, message=str(e))

    # ── 初始化辅助 ────────────────────────────────────────────

    def _init_db_audit(self, run_id: str) -> Any:
        if not self.audit_provider:
            return None
        from long_earn.backtest.engine.audit import AuditLogger  # noqa: PLC0415

        return AuditLogger(self.audit_provider, run_id)

    @staticmethod
    def _get_timestamps(full_data: pl.DataFrame) -> list[Any]:
        return (
            full_data.select("timestamp")
            .unique()
            .sort("timestamp")
            .to_series()
            .to_list()
        )

    # ── 单时间戳处理 ──────────────────────────────────────────

    def _process_timestamp(  # noqa: PLR0913
        self,
        ts: Any,
        guard: VisibilityGuard,
        portfolio: Portfolio,
        broker: Broker,
        strategy: BaseStrategy,
        db_audit: Any,
        bar_idx: int = 0,
    ) -> None:
        guard.set_time(ts)
        slab = guard.read_current_slab()
        mkt_event = MarketDataEvent(
            timestamp=ts,
            trace_id=str(uuid.uuid4()),
            event_id=f"mkt_{ts.isoformat()}",
            slab=slab,
        )

        portfolio.update_market_values(slab)

        # 检查待成交订单（限价/止损单）
        price_lookup = {
            sym: float(price)
            for sym, price in zip(
                slab.select("symbol").to_series().to_list(),
                slab.select("close").to_series().to_list(),
            )
        }
        pending_fills = broker.check_pending_orders(
            bar_idx=bar_idx, price_lookup=price_lookup
        )
        for pf in pending_fills:
            portfolio.update_from_fill(pf)
            self._log_audit(
                "FILL",
                pf.trace_id,
                f"pend_{pf.order_id}",
                "Broker",
                "SUCCESS",
                {
                    "symbol": pf.symbol,
                    "type": pf.order_type,
                    "price": pf.fill_price,
                    "quantity": pf.fill_quantity,
                    "from_pending": True,
                    "portfolio_value": portfolio.total_value,
                },
                db_audit,
            )

        risk_triggered = self._run_risk_checks(portfolio, slab, ts, broker)
        self._log_audit(
            "MARKET_DATA",
            mkt_event.trace_id,
            None,
            "Engine",
            "SUCCESS",
            {
                "timestamp": ts,
                "portfolio_value": portfolio.total_value,
                "strategy_state": strategy._state,
                "risk_triggered": risk_triggered,
            },
            db_audit,
        )

        if risk_triggered:
            return

        signal_event = strategy.on_bar(slab, guard.get_context())
        if signal_event is None:
            return

        self._log_audit(
            "SIGNAL",
            signal_event.trace_id,
            mkt_event.trace_id,
            "Strategy",
            "SUCCESS",
            {
                "signals": str(signal_event.signals),
                "strategy_id": signal_event.strategy_id,
            },
            db_audit,
        )

        self._execute_signals(signal_event, portfolio, slab, broker, db_audit)

    # ── 风控检查 ──────────────────────────────────────────────

    def _run_risk_checks(
        self,
        portfolio: Portfolio,
        slab: pl.DataFrame,
        ts: Any,
        broker: Broker,
    ) -> bool:
        """执行止损 + 最大回撤检查，返回是否触发风控"""
        triggered = False
        if self.stop_loss is not None:
            triggered = self._check_stop_loss(portfolio, slab, ts, broker)
        if self.max_drawdown_limit is not None and not triggered:
            triggered = self._check_max_drawdown(portfolio, slab, ts, broker)
        return triggered

    def _check_stop_loss(
        self,
        portfolio: Portfolio,
        slab: pl.DataFrame,
        ts: Any,
        broker: Broker,
    ) -> bool:
        assert self.stop_loss is not None
        triggered = False
        for symbol, pos in list(portfolio.positions.items()):
            pnl_pct = (
                (pos.current_price - pos.avg_cost) / pos.avg_cost
                if pos.avg_cost > 0
                else 0.0
            )
            if pnl_pct > -self.stop_loss:
                continue

            price = self._lookup_price(slab, symbol)
            if price is not None:
                order = OrderEvent(
                    timestamp=ts,
                    trace_id=str(uuid.uuid4()),
                    event_id=f"sl_{ts.isoformat()}_{symbol}",
                    symbol=symbol,
                    order_type="SELL",
                    quantity=pos.shares,
                    price=price,
                )
                fill = broker.execute_order(order, price)
                portfolio.update_from_fill(fill)
            triggered = True
        return triggered

    def _check_max_drawdown(
        self,
        portfolio: Portfolio,
        slab: pl.DataFrame,
        ts: Any,
        broker: Broker,
    ) -> bool:
        assert self.max_drawdown_limit is not None
        peak_value = (
            max(portfolio.equity_curve)
            if portfolio.equity_curve
            else portfolio.total_value
        )
        dd = (
            (portfolio.total_value - peak_value) / peak_value if peak_value > 0 else 0.0
        )
        if dd > -self.max_drawdown_limit:
            return False

        for symbol, pos in list(portfolio.positions.items()):
            price = self._lookup_price(slab, symbol)
            if price is not None:
                order = OrderEvent(
                    timestamp=ts,
                    trace_id=str(uuid.uuid4()),
                    event_id=f"dd_{ts.isoformat()}_{symbol}",
                    symbol=symbol,
                    order_type="SELL",
                    quantity=pos.shares,
                    price=price,
                )
                fill = broker.execute_order(order, price)
                portfolio.update_from_fill(fill)
        return True

    @staticmethod
    def _lookup_price(slab: pl.DataFrame, symbol: str) -> float | None:
        price_series = (
            slab.filter(pl.col("symbol") == symbol).select("close").to_series()
        )
        if price_series.is_empty():
            return None
        return price_series[0]  # type: ignore[no-any-return]

    # ── 信号执行 ──────────────────────────────────────────────

    def _execute_signals(
        self,
        signal_event: Any,
        portfolio: Portfolio,
        slab: pl.DataFrame,
        broker: Broker,
        db_audit: Any,
    ) -> None:
        orders = portfolio.process_signal(signal_event, slab, self.max_positions)
        for order in orders:
            self._log_audit(
                "ORDER",
                order.trace_id,
                signal_event.trace_id,
                "Portfolio",
                "SUCCESS",
                {
                    "symbol": order.symbol,
                    "type": order.order_type,
                    "quantity": order.quantity,
                },
                db_audit,
            )

            price = self._lookup_price(slab, order.symbol)
            if price is None:
                continue

            fill = broker.execute_order(order, price)
            portfolio.update_from_fill(fill)

            self._log_audit(
                "FILL",
                fill.trace_id,
                order.trace_id,
                "Broker",
                "SUCCESS",
                {
                    "symbol": fill.symbol,
                    "type": fill.order_type,
                    "price": fill.fill_price,
                    "quantity": fill.fill_quantity,
                    "portfolio_value": portfolio.total_value,
                },
                db_audit,
            )

    # ── 审计日志 ──────────────────────────────────────────────

    def _log_audit(  # noqa: PLR0913
        self,
        event_type: str,
        trace_id: str,
        parent_id: str | None,
        component: str,
        status: str,
        payload: dict[str, Any],
        db_audit: Any,
    ) -> None:
        entry = {
            "event_type": event_type,
            "trace_id": trace_id,
            "parent_id": parent_id,
            "component": component,
            "status": status,
            "payload": payload,
        }
        self.audit_logger.log_transition(**entry)
        if db_audit:
            db_audit.log_transition(
                event_type=event_type,
                trace_id=trace_id,
                parent_id=parent_id,
                component=component,
                status=status,
                payload=payload,
            )

    # ── 最终处理 ──────────────────────────────────────────────

    @staticmethod
    def _finalize_mark_to_market(
        portfolio: Portfolio,
        full_data: pl.DataFrame,
        last_ts: Any,
    ) -> None:
        portfolio.update_market_values(full_data.filter(pl.col("timestamp") == last_ts))

    def _build_result(
        self,
        portfolio: Portfolio,
        trading_days: int,
        full_data: pl.DataFrame | None = None,
        benchmark_symbol: str = "",
    ) -> BacktestResult:
        metrics = self._calculate_metrics(portfolio)
        bm = self._benchmark_or_none(
            full_data, benchmark_symbol, portfolio.equity_curve
        )

        return BacktestResult(
            success=True,
            message="回测成功",
            total_return=metrics.total_return,
            annual_return=metrics.annual_return,
            sharpe_ratio=metrics.sharpe_ratio,
            max_drawdown=metrics.max_drawdown,
            win_rate=metrics.win_rate,
            trading_days=trading_days,
            volatility=metrics.volatility,
            calmar_ratio=metrics.calmar_ratio,
            sortino_ratio=metrics.sortino_ratio,
            alpha=bm["alpha"],
            beta=bm["beta"],
            information_ratio=bm["information_ratio"],
            tracking_error=bm["tracking_error"],
            benchmark_return=bm["benchmark_return"],
            daily_returns=[
                {"day": i, "value": v} for i, v in enumerate(portfolio.equity_curve)
            ],
            trade_count=portfolio.trade_count,
            attribution=dict(portfolio.pnl_by_symbol),
        )

    @staticmethod
    def _benchmark_or_none(
        full_data: pl.DataFrame | None,
        benchmark_symbol: str,
        equity_curve: list[float],
    ) -> dict[str, float]:
        if full_data is None or not benchmark_symbol:
            return _empty_bm()
        return EventDrivenBacktestEngine._calculate_benchmark_metrics(
            equity_curve,
            full_data,
            benchmark_symbol,
        )

    # ── 基准对比 ──────────────────────────────────────────────

    @staticmethod
    def _calculate_benchmark_metrics(
        equity_curve: list[float],
        full_data: pl.DataFrame,
        benchmark_symbol: str,
    ) -> dict[str, float]:
        """计算 Alpha、Beta、信息比率等基准对比指标"""
        bm_data = (
            full_data.filter(pl.col("symbol") == benchmark_symbol)
            .sort("timestamp")
            .select("close")
            .to_series()
            .to_list()
        )
        if len(bm_data) < EventDrivenBacktestEngine.MIN_BM_POINTS or not bm_data[0]:
            return _empty_bm()

        bm_prices = [float(p) for p in bm_data if p is not None]
        if len(bm_prices) < EventDrivenBacktestEngine.MIN_BM_POINTS:
            return _empty_bm()

        n = min(len(equity_curve), len(bm_prices))
        if n < EventDrivenBacktestEngine.MIN_BM_POINTS:
            return _empty_bm()

        eq_trimmed = np.array(equity_curve[:n], dtype=float)
        bm_trimmed = np.array(bm_prices[:n], dtype=float)

        port_returns = np.diff(eq_trimmed) / eq_trimmed[:-1]
        bm_returns = np.diff(bm_trimmed) / bm_trimmed[:-1]

        if len(port_returns) < EventDrivenBacktestEngine.MIN_TRADING_DAYS:
            return {
                "alpha": 0.0,
                "beta": 0.0,
                "information_ratio": 0.0,
                "tracking_error": 0.0,
                "benchmark_return": float(bm_returns[-1]),
            }

        excess = port_returns - bm_returns
        cov = float(np.cov(port_returns, bm_returns)[0, 1])
        var_bm = float(np.var(bm_returns, ddof=1))
        beta = cov / var_bm if var_bm > 0 else 0.0

        annual_excess = float(np.mean(excess)) * 252
        tracking_error = float(np.std(excess, ddof=1)) * np.sqrt(252)
        information_ratio = (
            annual_excess / tracking_error if tracking_error > 0 else 0.0
        )
        alpha = annual_excess
        benchmark_return = float((bm_prices[-1] / bm_prices[0]) - 1)

        return {
            "alpha": round(alpha, 6),
            "beta": round(beta, 4),
            "information_ratio": round(information_ratio, 4),
            "tracking_error": round(tracking_error, 6),
            "benchmark_return": round(benchmark_return, 6),
        }

    # ── Walk-Forward 回测 ────────────────────────────────────

    def walk_forward_run(  # noqa: PLR0913
        self,
        strategy: BaseStrategy,
        start_date: str,
        end_date: str,
        symbols: list[str],
        n_splits: int = 3,
        benchmark_symbol: str = "",
    ) -> dict[str, Any]:
        """执行 Walk-Forward 滚动回测（自动样本外验证）

        Args:
            strategy: 策略实例（每次折叠的 init() 会被调用）
            start_date: 起始日期
            end_date: 结束日期
            symbols: 候选股票列表
            n_splits: 时间窗折叠数
            benchmark_symbol: 基准指数代码

        Returns:
            {
                "fold_results": [{fold_id, train, test}],
                "average_metrics": {train: {}, test: {}},
                "n_splits": n,
            }
        """

        full_data = self._prepare_data(symbols, start_date, end_date)
        if full_data.is_empty():
            return {"error": "加载数据为空"}

        timestamps = self._get_timestamps(full_data)
        splitter = TimeSeriesSplit(n_splits=n_splits)
        splits = splitter.split(timestamps)

        fold_results: list[dict[str, Any]] = []
        all_train_metrics: list[dict[str, float]] = []
        all_test_metrics: list[dict[str, float]] = []

        for fold_idx, (train_ts, test_ts) in enumerate(splits):
            train_start = str(train_ts[0])
            train_end = str(train_ts[-1])
            test_start = str(test_ts[0]) if test_ts else train_end
            test_end = str(test_ts[-1]) if test_ts else train_end

            # 训练期回测
            strategy.init()
            train_result = self.run(
                strategy,
                train_start,
                train_end,
                symbols,
                benchmark_symbol,
            )
            train_metrics = {
                "total_return": train_result.total_return or 0.0,
                "sharpe_ratio": train_result.sharpe_ratio or 0.0,
                "max_drawdown": train_result.max_drawdown or 0.0,
                "alpha": train_result.alpha or 0.0,
            }
            all_train_metrics.append(train_metrics)

            # 测试期回测
            test_result = self.run(
                strategy,
                test_start,
                test_end,
                symbols,
                benchmark_symbol,
            )
            test_metrics = {
                "total_return": test_result.total_return or 0.0,
                "sharpe_ratio": test_result.sharpe_ratio or 0.0,
                "max_drawdown": test_result.max_drawdown or 0.0,
                "alpha": test_result.alpha or 0.0,
            }
            all_test_metrics.append(test_metrics)

            fold_results.append(
                {
                    "fold_id": fold_idx,
                    "train": {"start": train_start, "end": train_end, **train_metrics},
                    "test": {"start": test_start, "end": test_end, **test_metrics},
                }
            )

        def _avg(metrics_list: list[dict[str, float]]) -> dict[str, float]:
            if not metrics_list:
                return {}
            return {
                k: float(np.mean([m[k] for m in metrics_list])) for k in metrics_list[0]
            }

        return {
            "fold_results": fold_results,
            "average_metrics": {
                "train": _avg(all_train_metrics),
                "test": _avg(all_test_metrics),
            },
            "n_splits": n_splits,
        }

    # ── 数据与指标 ────────────────────────────────────────────

    def _prepare_data(self, symbols: list[str], start: str, end: str) -> pl.DataFrame:
        """准备 Polars 格式的数据面板"""
        if self.data_provider is not None:
            df = self.data_provider.get_merged_panel_as_polars(symbols, start, end)
            if df is not None and not df.is_empty():
                return df  # type: ignore[no-any-return]
        return pl.DataFrame()

    def _calculate_metrics(self, portfolio: Portfolio) -> PerformanceMetrics:
        """计算最终绩效指标"""
        equity = portfolio.equity_curve
        if len(equity) < self.MIN_TRADING_DAYS:
            return PerformanceMetrics()

        returns = np.diff(equity) / equity[:-1]
        if len(returns) == 0:
            return PerformanceMetrics()

        total_return = (equity[-1] / equity[0]) - 1
        trading_days = len(returns)
        annual_factor = 252 / trading_days if trading_days > 0 else 1.0
        annual_return = (1 + total_return) ** annual_factor - 1
        volatility = float(np.std(returns, ddof=1)) * np.sqrt(252)
        sharpe = annual_return / volatility if volatility > 0 else 0.0

        peak = np.maximum.accumulate(equity)
        drawdown = (equity - peak) / peak
        max_dd = float(np.min(drawdown))

        win_rate = (
            float(np.sum(returns > 0) / len(returns)) if len(returns) > 0 else 0.0
        )
        calmar = annual_return / abs(max_dd) if max_dd != 0 else 0.0

        downside = returns[returns < 0]
        downside_std = (
            float(np.std(downside, ddof=1)) * np.sqrt(252) if len(downside) > 0 else 0.0
        )
        sortino = annual_return / downside_std if downside_std > 0 else 0.0

        return PerformanceMetrics(
            total_return=total_return,
            annual_return=annual_return,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            win_rate=win_rate,
            trading_days=trading_days,
            volatility=volatility,
            calmar_ratio=calmar,
            sortino_ratio=sortino,
        )
