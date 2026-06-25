"""事件驱动回测引擎核心

实现 T 维度迭代 × S 维度向量化 (Slab) 的执行链路。
"""

import contextlib
import uuid
from typing import Any

import numpy as np
import polars as pl
from loguru import logger

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
        audit_logger: InMemoryAuditTrail | None = None,
    ):
        self.data_provider = data_provider
        self.universe_provider = universe_provider
        self.cost_config = cost_config or TradingCostConfig()
        self.audit_provider = audit_provider
        self.audit_logger = audit_logger or InMemoryAuditTrail()
        self.stop_loss = stop_loss
        self.max_drawdown_limit = max_drawdown_limit
        self.max_position_pct = max_position_pct
        self.max_positions = max_positions

    # ── 主入口 ────────────────────────────────────────────────

    def run(  # noqa: PLR0913
        self,
        strategy: BaseStrategy,
        start_date: str,
        end_date: str,
        symbols: list[str],
        benchmark_symbol: str = "",
        full_data: pl.DataFrame | None = None,
    ) -> BacktestResult:
        """执行回测

        Args:
            strategy: 策略实例
            start_date: 起始日期
            end_date: 结束日期
            symbols: 候选股票列表
            benchmark_symbol: 基准指数代码（如 "000300"），用于计算 Alpha/Beta 等
            full_data: 预加载的完整数据面板；传入则跳过 _prepare_data()，适合并行回测
        """
        try:
            if full_data is None:
                full_data = self._prepare_data(symbols, start_date, end_date)
            else:
                # 防御性日期过滤：即使外部传入 full_data，也确保只取 [start_date, end_date] 范围
                date_col = "timestamp" if "timestamp" in full_data.columns else "date"
                start_dt = pl.lit(start_date).str.to_datetime()
                end_dt = pl.lit(end_date).str.to_datetime()
                full_data = full_data.filter(
                    (pl.col(date_col) >= start_dt) & (pl.col(date_col) <= end_dt)
                )
            if full_data.is_empty():
                return BacktestResult(success=False, message="加载数据为空")

            guard = VisibilityGuard(full_data)
            portfolio = Portfolio(cost_config=self.cost_config)
            broker = Broker(self.cost_config)
            broker.reset()
            strategy.init()

            run_id = str(uuid.uuid4())
            self.audit_logger.trail.clear()
            db_audit = self._init_db_audit(run_id)

            timestamps = self._get_timestamps(full_data)

            for _bar_idx, ts in enumerate(timestamps):
                self._process_timestamp(
                    ts, guard, portfolio, broker, strategy, db_audit
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
            return BacktestResult(
                success=False, message=str(e), error_category="engine_error"
            )

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
        price_lookup = {}
        for sym, price in zip(
            slab.select("symbol").to_series().to_list(),
            slab.select("close").to_series().to_list(),
            strict=True,
        ):
            if price is not None:
                with contextlib.suppress(TypeError, ValueError):
                    price_lookup[sym] = float(price)
        pending_fills = broker.check_pending_orders(
            price_lookup=price_lookup
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

        # 待成交订单可能影响 portfolio.total_value，先更新市值再做风控
        portfolio.update_market_values(slab)

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

        if not risk_triggered:
            signal_event = strategy.on_bar(slab, guard.get_context())
            if signal_event is not None:
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

        # Bar 末尾：记录净值曲线（反映所有交易和市值变动后的终值）
        portfolio.update_market_values(slab)
        portfolio._sync_equity_curve()

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
            # 触发判断：用日内最低价确认是否触及止损线（真实止损单监控盘中价格）
            low_price = self._lookup_price(slab, symbol, field="low")
            close_price = self._lookup_price(slab, symbol, field="close")
            check_price = low_price if (low_price and low_price > 0) else close_price
            if check_price is None or check_price <= 0:
                continue

            pnl_pct = (
                (check_price - pos.avg_cost) / pos.avg_cost
                if pos.avg_cost > 0 else 0.0
            )
            if pnl_pct > -self.stop_loss:
                continue

            # 成交价：用"止损线"作为基准而非日内最低价（避免给回测白送日内极值）
            # 现实中止损单触发后通常以触发价附近 + 滑点成交，绝不会恰好 = 日内 low
            stop_threshold = pos.avg_cost * (1 - self.stop_loss)
            # 取 max(止损线, 日内最低价): 真实成交不会优于止损线
            ref_price = max(stop_threshold, check_price) if check_price else stop_threshold
            if ref_price > 0:
                order = OrderEvent(
                    timestamp=ts,
                    trace_id=str(uuid.uuid4()),
                    event_id=f"sl_{ts.isoformat()}_{symbol}",
                    symbol=symbol,
                    order_type="SELL",
                    quantity=pos.shares,
                    price=ref_price,
                )
                # broker.execute_order 内部 _fill_market 会按 (1 - slip) 进一步扣减
                fill = broker.execute_order(order, ref_price)
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
        peak_value = portfolio.peak_value
        dd = (
            (portfolio.total_value - peak_value) / peak_value if peak_value > 0 else 0.0
        )
        # max_drawdown_limit 为正数时表示允许的最大回撤幅度（如 0.15 = 15%）
        # dd 为负数或零，当 dd < -limit 时触发（回撤超过限制）
        threshold = -abs(self.max_drawdown_limit)
        if dd > threshold:
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
    def _lookup_price(slab: pl.DataFrame, symbol: str, field: str = "close") -> float | None:
        """从 slab 中查找指定字段的价格"""
        if field not in slab.columns:
            return None
        price_series = (
            slab.filter(pl.col("symbol") == symbol).select(field).to_series()
        )
        if price_series.is_empty():
            return None
        result = price_series[0]
        return float(result) if result is not None else None

    # ── 信号执行 ──────────────────────────────────────────────

    def _execute_signals(
        self,
        signal_event: Any,
        portfolio: Portfolio,
        slab: pl.DataFrame,
        broker: Broker,
        db_audit: Any,
    ) -> None:
        orders = portfolio.process_signal(
            signal_event, slab, self.max_positions, self.max_position_pct
        )
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
        """最终市值结算：更新持仓市值至最后一根 bar 的收盘价"""
        portfolio.update_market_values(full_data.filter(pl.col("timestamp") == last_ts))
        # equity_curve 已在 _process_timestamp 末尾通过 _sync_equity_curve 记录，
        # 此处仅需确保 total_value 反映最终市值（供 _build_result 使用）
        if portfolio.equity_curve:
            portfolio.equity_curve[-1] = portfolio.total_value

    def _build_result(
        self,
        portfolio: Portfolio,
        trading_days: int,
        full_data: pl.DataFrame | None = None,
        benchmark_symbol: str = "",
    ) -> BacktestResult:
        # 数据可信度门槛：交易日数 / equity_curve 长度不足时拒绝输出指标，
        # 防止把"全程持仓未变 → 零收益"误标为成功的回测结果。
        equity_len = len(portfolio.equity_curve)
        if trading_days < self.MIN_TRADING_DAYS or equity_len < self.MIN_TRADING_DAYS:
            return BacktestResult(
                success=False,
                message=(
                    f"回测样本不足：trading_days={trading_days}, "
                    f"equity_points={equity_len}，最少需要 {self.MIN_TRADING_DAYS}"
                ),
                error_category="insufficient_data",
                error_detail=(
                    "样本量低于最低交易日阈值，无法可信地计算 Sharpe/MaxDD 等指标。"
                    "请检查数据源或扩大回测区间。"
                ),
                trading_days=trading_days,
            )

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
        """计算 Alpha、Beta、信息比率等基准对比指标

        通过时间戳对齐确保组合权益曲线与基准价格序列严格对应，
        避免因基准数据缺失导致时序错位。

        Alpha 使用 Jensen's Alpha 公式: α = R_p - β · R_m (假设 R_f = 0)
        """
        if not benchmark_symbol:
            return _empty_bm()

        # 提取基准数据的 timestamp → close 映射
        bm_df = full_data.filter(pl.col("symbol") == benchmark_symbol).sort("timestamp")
        if bm_df.height < EventDrivenBacktestEngine.MIN_BM_POINTS:
            return _empty_bm()

        bm_ts = bm_df.select("timestamp").to_series().to_list()
        bm_close = bm_df.select("close").to_series().to_list()
        bm_price_map: dict[Any, float] = {}
        for ts, price in zip(bm_ts, bm_close, strict=True):
            if ts is not None and price is not None:
                bm_price_map[ts] = float(price)

        if len(bm_price_map) < EventDrivenBacktestEngine.MIN_BM_POINTS:
            return _empty_bm()

        # 按组合的时间戳序列对齐权益曲线与基准价格
        timestamps = EventDrivenBacktestEngine._get_timestamps(full_data)

        eq_aligned: list[float] = []
        bm_aligned: list[float] = []
        for i, ts in enumerate(timestamps):
            if ts in bm_price_map and i < len(equity_curve):
                eq_aligned.append(equity_curve[i])
                bm_aligned.append(bm_price_map[ts])

        if len(eq_aligned) < EventDrivenBacktestEngine.MIN_BM_POINTS:
            return _empty_bm()

        eq_arr = np.array(eq_aligned, dtype=float)
        bm_arr = np.array(bm_aligned, dtype=float)

        port_returns = np.diff(eq_arr) / eq_arr[:-1]
        bm_returns = np.diff(bm_arr) / bm_arr[:-1]

        if len(port_returns) < EventDrivenBacktestEngine.MIN_TRADING_DAYS:
            return {
                "alpha": 0.0,
                "beta": 0.0,
                "information_ratio": 0.0,
                "tracking_error": 0.0,
                "benchmark_return": float((bm_arr[-1] / bm_arr[0]) - 1) if bm_arr[0] > 0 else 0.0,
            }

        # Beta: Cov(R_p, R_m) / Var(R_m)
        cov = float(np.cov(port_returns, bm_returns)[0, 1])
        var_bm = float(np.var(bm_returns, ddof=1))
        beta = cov / var_bm if var_bm > 0 else 0.0

        # 年化收益率（算术年化，与夏普比率分母保持一致）
        port_annual = float(np.mean(port_returns)) * 252
        bm_annual = float(np.mean(bm_returns)) * 252

        # Jensen's Alpha: α = R_p - β · R_m (R_f = 0)
        alpha = port_annual - beta * bm_annual

        # 信息比率
        excess = port_returns - bm_returns
        tracking_error = float(np.std(excess, ddof=1)) * np.sqrt(252)
        information_ratio = (
            alpha / tracking_error if tracking_error > 0 else 0.0
        )
        benchmark_return = float((bm_arr[-1] / bm_arr[0]) - 1) if bm_arr[0] > 0 else 0.0

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
                "failed_folds": [{fold_id, phase, error_category, message}],
            }

        可信度承诺：失败的 fold（success=False / insufficient_data / engine_error）
        不进入 average_metrics 计算，避免把失败的 0 当作平均业绩的一部分。
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
        failed_folds: list[dict[str, Any]] = []

        # 保存当前审计日志，Walk-Forward 完成后恢复
        saved_audit_trail = self.audit_logger.trail.copy()

        for fold_idx, (train_ts, test_ts) in enumerate(splits):
            train_start = str(train_ts[0])
            train_end = str(train_ts[-1])
            test_start = str(test_ts[0]) if test_ts else train_end
            test_end = str(test_ts[-1]) if test_ts else train_end

            # 训练期回测（每个 fold 使用独立的审计日志）
            self.audit_logger.trail.clear()
            strategy.init()
            train_result = self.run(
                strategy, train_start, train_end, symbols, benchmark_symbol,
                full_data=full_data,
            )
            if train_result.success:
                train_metrics = {
                    "total_return": train_result.total_return or 0.0,
                    "sharpe_ratio": train_result.sharpe_ratio or 0.0,
                    "max_drawdown": train_result.max_drawdown or 0.0,
                    "alpha": train_result.alpha or 0.0,
                }
                all_train_metrics.append(train_metrics)
            else:
                train_metrics = {"error": train_result.message}
                failed_folds.append({
                    "fold_id": fold_idx,
                    "phase": "train",
                    "error_category": train_result.error_category or "unknown",
                    "message": train_result.message,
                })

            # 测试期回测（重置策略状态和审计日志，防止训练期信息泄漏）
            self.audit_logger.trail.clear()
            strategy.init()
            test_result = self.run(
                strategy, test_start, test_end, symbols, benchmark_symbol,
                full_data=full_data,
            )
            if test_result.success:
                test_metrics = {
                    "total_return": test_result.total_return or 0.0,
                    "sharpe_ratio": test_result.sharpe_ratio or 0.0,
                    "max_drawdown": test_result.max_drawdown or 0.0,
                    "alpha": test_result.alpha or 0.0,
                }
                all_test_metrics.append(test_metrics)
            else:
                test_metrics = {"error": test_result.message}
                failed_folds.append({
                    "fold_id": fold_idx,
                    "phase": "test",
                    "error_category": test_result.error_category or "unknown",
                    "message": test_result.message,
                })

            fold_results.append(
                {
                    "fold_id": fold_idx,
                    "train": {"start": train_start, "end": train_end, **train_metrics},
                    "test": {"start": test_start, "end": test_end, **test_metrics},
                }
            )

        # 恢复原始审计日志
        self.audit_logger.trail = saved_audit_trail

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
            "failed_folds": failed_folds,
        }

    # ── 数据与指标 ────────────────────────────────────────────

    def _prepare_data(self, symbols: list[str], start: str, end: str) -> pl.DataFrame:
        """准备 Polars 格式的数据面板（引擎层防御性过滤日期范围）"""
        if self.data_provider is not None:
            df = self.data_provider.get_merged_panel_as_polars(symbols, start, end)
            if df is not None and not df.is_empty():
                # 防御性过滤：确保数据不超出请求的日期范围，防止 Walk-Forward 等场景下的数据泄漏
                if "timestamp" in df.columns:
                    start_dt = pl.lit(start).str.to_datetime()
                    end_dt = pl.lit(end).str.to_datetime()
                    df = df.filter(
                        (pl.col("timestamp") >= start_dt) & (pl.col("timestamp") <= end_dt)
                    )
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
        # 算术年化：与分母 std(returns) * sqrt(252) 保持一致
        annual_return = float(np.mean(returns)) * 252
        volatility = float(np.std(returns, ddof=1)) * np.sqrt(252)
        sharpe = annual_return / volatility if volatility > 0 else 0.0

        peak = np.maximum.accumulate(equity)
        drawdown = (equity - peak) / peak
        max_dd = float(np.min(drawdown))

        # 交易胜率基于日收益率统计（因当前架构未逐笔标记盈亏）
        win_rate = (
            float(np.sum(returns > 0) / len(returns)) if len(returns) > 0 else 0.0
        )
        calmar = annual_return / abs(max_dd) if max_dd != 0 else 0.0

        downside = returns[returns < 0]
        # 使用标准下行偏差公式: sqrt(mean(R^2)) for R < 0，不减去均值
        downside_std = (
            float(np.sqrt(np.mean(downside ** 2))) * np.sqrt(252)
            if len(downside) > 0
            else 0.0
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
