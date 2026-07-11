"""事件驱动回测引擎核心

实现 T 维度迭代 × S 维度向量化 (Slab) 的执行链路。
"""

import contextlib
import math
import time
import uuid
from datetime import datetime
from typing import Any

import numpy as np
import polars as pl
from loguru import logger

from long_earn.backtest.domain.entities import (
    MarketDataEvent,
    OrderEvent,
    PerformanceMetrics,
    SignalEvent,
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
        take_profit: float | None = None,
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
        self.take_profit = take_profit
        self.max_drawdown_limit = max_drawdown_limit
        self.max_position_pct = max_position_pct
        self.max_positions = max_positions
        # 当前回测 run_id / db_audit：存为实例变量让内部方法（风控/结果构建等）
        # 无需透传即可写审计日志。每次 run() 开头重置。
        self._current_run_id: str = ""
        self._db_audit: Any = None
        # T+1 信号队列：策略在 T 日产生的信号在 T+1 日以 open 价执行，
        # 消除 T 日 close 决策 + close 成交的前视偏差（P0-05 修复）。
        self._pending_signals: list[SignalEvent] = []
        # 涨跌停板追踪：记录上一 bar 的 close，用于计算本 bar 的涨跌停价格（P0-07 修复）。
        self._prev_close_map: dict[str, float] = {}
        self._current_limit_up_map: dict[str, float] = {}
        self._current_limit_down_map: dict[str, float] = {}
        # metrics_unreliable 检测（P0-03）：跟踪回测过程中的退化信号
        self._total_orders: int = 0
        self._total_skipped: int = 0
        self._total_partial_fills: int = 0

    @staticmethod
    def _compute_price_limits(
        prev_close: float,
    ) -> tuple[float, float]:
        """计算 A 股涨跌停价格（10% 涨跌幅限制）。

        Args:
            prev_close: 前收盘价

        Returns:
            (limit_up, limit_down) 涨停价和跌停价
        """
        if prev_close <= 0:
            return float("inf"), 0.0
        return round(prev_close * 1.1, 2), round(prev_close * 0.9, 2)

    def _pre_trade_check(
        self,
        order: Any,
        price: float,
        signal_trace_id: str,  # noqa: ARG002
        db_audit: Any,  # noqa: ARG002
        slab: pl.DataFrame | None = None,
    ) -> str | None:
        """Pre-trade 单笔风控门（P0-08）：聚合多项合规检查。

        在撮合前执行，返回 None 表示通过，返回字符串表示跳过原因。
        覆盖检查项：
          - 涨跌停板（P0-07）：涨停拒买、跌停拒卖
          - 价格有效性：NaN / Inf / 非正数
          - 停牌检查（P1-09）：成交量为 0 时视为停牌，禁止交易

        T+1 约束（P0-06）在 Portfolio._compute_order_infos 中完成，
        成交量限制（P0-04）在 Broker._fill_market 中完成。
        """
        if price is None or price <= 0 or not math.isfinite(price):
            return f"pre_trade_price_invalid:{order.symbol} price={price}"

        limit_reason = self._check_limit_up_down(
            order.symbol, order.order_type, price
        )
        if limit_reason is not None:
            return limit_reason

        # 停牌检查（P1-09）：成交量 = 0 时视为停牌
        if slab is not None:
            volume = self._lookup_price(slab, order.symbol, field="volume")
            if volume is not None and volume == 0:
                return f"停牌拒单:{order.symbol} 当日成交量为 0（疑似停牌）"

        return None

    def _check_limit_up_down(
        self,
        symbol: str,
        order_type: str,
        price: float,
    ) -> str | None:
        """检查涨跌停板约束。返回 None 表示通过，返回字符串表示跳过原因。"""
        if symbol not in self._current_limit_up_map:
            return None
        limit_up = self._current_limit_up_map.get(symbol, float("inf"))
        limit_down = self._current_limit_down_map.get(symbol, 0.0)
        if order_type == "BUY" and price >= limit_up:
            return f"涨停拒买:{symbol} price={price:.2f} >= limit_up={limit_up:.2f}"
        if order_type == "SELL" and price <= limit_down:
            return f"跌停拒卖:{symbol} price={price:.2f} <= limit_down={limit_down:.2f}"
        return None

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
        # run_id 提前生成：数据为空 / 异常等失败路径也要能审计
        run_id = str(uuid.uuid4())
        self._current_run_id = run_id
        self.audit_logger.trail.clear()
        self._db_audit = self._init_db_audit(run_id)
        db_audit = self._db_audit
        run_start_ts = time.perf_counter()
        # 重置可信度追踪计数器（P0-03）
        self._total_orders = 0
        self._total_skipped = 0
        self._total_partial_fills = 0

        # RUN_START：记录回测配置，让审计日志能独立重建本次回测的输入参数
        self._log_audit(
            "RUN_START",
            run_id,
            None,
            "Engine",
            "SUCCESS",
            {
                "start_date": start_date,
                "end_date": end_date,
                "symbols_count": len(symbols),
                "benchmark_symbol": benchmark_symbol,
                "stop_loss": self.stop_loss,
                "max_drawdown_limit": self.max_drawdown_limit,
                "max_position_pct": self.max_position_pct,
                "max_positions": self.max_positions,
                "strategy_id": getattr(strategy, "strategy_id", ""),
            },
            db_audit,
        )

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
                # G6: 数据为空必须审计，否则失败路径链路断裂
                self._log_audit(
                    "DATA_EMPTY",
                    str(uuid.uuid4()),
                    run_id,
                    "Engine",
                    "FAILED",
                    {"message": "加载数据为空", "symbols_count": len(symbols)},
                    db_audit,
                )
                return BacktestResult(success=False, message="加载数据为空")

            guard = VisibilityGuard(full_data)
            portfolio = Portfolio(cost_config=self.cost_config)
            broker = Broker(self.cost_config)
            broker.reset()
            strategy.init()

            timestamps = self._get_timestamps(full_data)

            for _bar_idx, ts in enumerate(timestamps):
                self._process_timestamp(
                    ts, guard, portfolio, broker, strategy, db_audit
                )

            self._finalize_mark_to_market(portfolio, full_data, timestamps[-1])

            # P0-03：判断指标是否可信
            # 条件：订单大量被跳过（>50%）或大量部分成交，说明回测状态异常
            metrics_unreliable = False
            if self._total_orders > 0:
                skip_ratio = self._total_skipped / self._total_orders
                if skip_ratio > 0.5 or self._total_partial_fills > self._total_orders * 0.5:  # noqa: PLR2004
                    metrics_unreliable = True

            result = self._build_result(
                portfolio,
                len(timestamps),
                full_data,
                benchmark_symbol,
                metrics_unreliable=metrics_unreliable,
            )

            # RUN_END：记录回测结果摘要（成功/失败都要记）
            self._log_audit(
                "RUN_END",
                str(uuid.uuid4()),
                run_id,
                "Engine",
                "SUCCESS" if result.success else "FAILED",
                {
                    "success": result.success,
                    "total_return": result.total_return,
                    "sharpe_ratio": result.sharpe_ratio,
                    "max_drawdown": result.max_drawdown,
                    "trade_count": result.trade_count,
                    "trading_days": result.trading_days,
                    "latency_ms": (time.perf_counter() - run_start_ts) * 1000,
                },
                db_audit,
                latency_ms=(time.perf_counter() - run_start_ts) * 1000,
            )
            return result

        except Exception as e:
            logger.exception("回测引擎执行失败")
            # G4: 异常必须审计，记录异常类型和消息，让审计链不断裂
            self._log_audit(
                "RUN_ERROR",
                str(uuid.uuid4()),
                run_id,
                "Engine",
                "FAILED",
                {
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "latency_ms": (time.perf_counter() - run_start_ts) * 1000,
                },
                db_audit,
            )
            return BacktestResult(
                success=False, message=str(e), error_category="engine_error"
            )
        finally:
            # 释放审计存储连接（DuckDB 连接需显式关闭，避免句柄泄漏）
            close = getattr(self.audit_provider, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()

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

        # 更新涨跌停价格（P0-07）：基于前收盘价计算当日涨跌停限价
        self._current_limit_up_map.clear()
        self._current_limit_down_map.clear()
        for row in slab.iter_rows(named=True):
            sym = row.get("symbol", "")
            close = row.get("close", None)
            if sym and close is not None:
                prev_close = self._prev_close_map.get(sym, close)
                up, down = self._compute_price_limits(prev_close)
                self._current_limit_up_map[sym] = up
                self._current_limit_down_map[sym] = down

        # ── T+1 信号执行：执行前一 bar 产生的 pending 信号，以当日 open 成交 ──
        # P0-05 修复：策略基于 T 日 close 产生的信号不会在当日 close 成交，
        # 而是进入 pending 队列，在 T+1 日以 open 价撮合，消除前视偏差。
        if self._pending_signals:
            pending = self._pending_signals
            self._pending_signals = []  # 清空，防止重复执行
            for sig in pending:
                self._log_audit(
                    "SIGNAL_EXECUTE_T1",
                    str(uuid.uuid4()),
                    sig.trace_id,
                    "Engine",
                    "SUCCESS",
                    {
                        "strategy_id": sig.strategy_id,
                        "signals": str(sig.signals),
                        "execute_timestamp": str(ts),
                        "price_field": "open",
                    },
                    db_audit,
                )
                self._execute_signals(sig, portfolio, slab, broker, db_audit, price_field="open")

        # 检查待成交订单（限价/止损单）
        price_lookup = {}
        for sym, price in zip(
            slab.select("close").to_series().to_list(),
            slab.select("close").to_series().to_list(),
            strict=True,
        ):
            if price is not None:
                with contextlib.suppress(TypeError, ValueError):
                    price_lookup[sym] = float(price)
        pending_fills = broker.check_pending_orders(price_lookup=price_lookup)
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
                # P0-05 修复：信号不立即执行，入队等待 T+1 日以 open 价撮合
                self._pending_signals.append(signal_event)
        else:
            # G13: 风控触发时策略被跳过，记录"本应产生信号但被风控抑制"
            self._log_audit(
                "SIGNAL_SKIPPED_BY_RISK",
                str(uuid.uuid4()),
                mkt_event.trace_id,
                "Engine",
                "SKIPPED",
                {"timestamp": str(ts), "reason": "risk_triggered"},
                db_audit,
            )

        # Bar 末尾：记录净值曲线（反映所有交易和市值变动后的终值）
        portfolio.update_market_values(slab)
        portfolio._sync_equity_curve()

        # 更新前收盘价（P0-07），供下一 bar 计算涨跌停限价
        for row in slab.iter_rows(named=True):
            sym = row.get("symbol", "")
            close = row.get("close", None)
            if sym and close is not None:
                self._prev_close_map[sym] = float(close)

    # ── 风控检查 ──────────────────────────────────────────────

    def _run_risk_checks(
        self,
        portfolio: Portfolio,
        slab: pl.DataFrame,
        ts: Any,
        broker: Broker,
    ) -> bool:
        """执行止损 + 止盈 + 最大回撤检查，返回是否触发风控"""
        triggered = False
        if self.stop_loss is not None:
            triggered = self._check_stop_loss(portfolio, slab, ts, broker)
        if self.take_profit is not None and not triggered:
            triggered = self._check_take_profit(portfolio, slab, ts, broker)
        if self.max_drawdown_limit is not None and not triggered:
            triggered = self._check_max_drawdown(portfolio, slab, ts, broker)
        return triggered

    def _check_take_profit(
        self,
        portfolio: Portfolio,
        slab: pl.DataFrame,
        ts: Any,
        broker: Broker,
    ) -> bool:
        """止盈检查（P1-05）：持仓盈利超过 take_profit 阈值时强制卖出。"""
        assert self.take_profit is not None
        triggered = False
        for symbol, pos in list(portfolio.positions.items()):
            high_price = self._lookup_price(slab, symbol, field="high")
            close_price = self._lookup_price(slab, symbol, field="close")
            check_price = high_price if (high_price and high_price > 0) else close_price
            if check_price is None or check_price <= 0:
                continue

            pnl_pct = (
                (check_price - pos.avg_cost) / pos.avg_cost if pos.avg_cost > 0 else 0.0
            )
            if pnl_pct < self.take_profit:
                continue

            self._log_audit(
                "RISK_TRIGGER",
                str(uuid.uuid4()),
                None,
                "RiskControl",
                "WARNING",
                {
                    "risk_type": "take_profit",
                    "symbol": symbol,
                    "avg_cost": pos.avg_cost,
                    "check_price": check_price,
                    "pnl_pct": pnl_pct,
                    "take_profit_threshold": self.take_profit,
                    "quantity": pos.shares,
                    "timestamp": str(ts),
                },
                self._db_audit,
            )
            order = OrderEvent(
                timestamp=ts,
                trace_id=str(uuid.uuid4()),
                event_id=f"tp_{ts.isoformat()}_{symbol}",
                symbol=symbol,
                order_type="SELL",
                quantity=pos.shares,
                price=check_price,
            )
            fill = broker.execute_order(order, check_price)
            portfolio.update_from_fill(fill)
            triggered = True
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
                (check_price - pos.avg_cost) / pos.avg_cost if pos.avg_cost > 0 else 0.0
            )
            if pnl_pct > -self.stop_loss:
                continue

            # 成交价：用"止损线"作为基准而非日内最低价（避免给回测白送日内极值）
            # 现实中止损单触发后通常以触发价附近 + 滑点成交，绝不会恰好 = 日内 low
            stop_threshold = pos.avg_cost * (1 - self.stop_loss)
            # 取 max(止损线, 日内最低价): 真实成交不会优于止损线
            ref_price = (
                max(stop_threshold, check_price) if check_price else stop_threshold
            )
            if ref_price > 0:
                # G2: 风控触发独立审计——记录触发原因/持仓详情/触发价格，
                # 让"为什么产生这个 SELL"可追溯
                self._log_audit(
                    "RISK_TRIGGER",
                    str(uuid.uuid4()),
                    None,
                    "RiskControl",
                    "WARNING",
                    {
                        "risk_type": "stop_loss",
                        "symbol": symbol,
                        "avg_cost": pos.avg_cost,
                        "check_price": check_price,
                        "pnl_pct": pnl_pct,
                        "stop_loss_threshold": self.stop_loss,
                        "ref_price": ref_price,
                        "quantity": pos.shares,
                        "timestamp": str(ts),
                    },
                    self._db_audit,
                )
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

        # G2: 最大回撤触发独立审计——记录触发时的回撤值/峰值/当前净值
        self._log_audit(
            "RISK_TRIGGER",
            str(uuid.uuid4()),
            None,
            "RiskControl",
            "WARNING",
            {
                "risk_type": "max_drawdown",
                "drawdown": dd,
                "peak_value": peak_value,
                "total_value": portfolio.total_value,
                "max_drawdown_limit": self.max_drawdown_limit,
                "timestamp": str(ts),
            },
            self._db_audit,
        )

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
    def _lookup_price(
        slab: pl.DataFrame, symbol: str, field: str = "close"
    ) -> float | None:
        """从 slab 中查找指定字段的价格"""
        if field not in slab.columns:
            return None
        price_series = slab.filter(pl.col("symbol") == symbol).select(field).to_series()
        if price_series.is_empty():
            return None
        result = price_series[0]
        return float(result) if result is not None else None

    # ── 信号执行 ──────────────────────────────────────────────

    def _execute_signals(  # noqa: PLR0913
        self,
        signal_event: Any,
        portfolio: Portfolio,
        slab: pl.DataFrame,
        broker: Broker,
        db_audit: Any,
        price_field: str = "close",
    ) -> None:
        orders = portfolio.process_signal(
            signal_event,
            slab,
            self.max_positions,
            self.max_position_pct,
            price_field=price_field,
        )
        # T+1 跳过订单记录：portfolio 将 T+1 锁定的卖出订单标记为 skipped，
        # 由引擎统一记 ORDER_SKIPPED 审计事件（P0-06 修复）。
        for skipped in portfolio._last_skipped_orders:
            self._total_skipped += 1
            self._log_audit(
                "ORDER_SKIPPED",
                str(uuid.uuid4()),
                signal_event.trace_id,
                "Portfolio",
                "SKIPPED",
                {
                    "symbol": skipped["symbol"],
                    "reason": skipped.get("skip_reason", "T+1 locked"),
                },
                db_audit,
            )
        portfolio._last_skipped_orders = []
        for order in orders:
            self._total_orders += 1
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

            price = self._lookup_price(slab, order.symbol, field=price_field)
            if price is None:
                self._total_skipped += 1
                # G3: 订单因找不到价格被静默跳过，必须审计
                self._log_audit(
                    "ORDER_SKIPPED",
                    str(uuid.uuid4()),
                    order.trace_id,
                    "Engine",
                    "SKIPPED",
                    {
                        "symbol": order.symbol,
                        "order_type": order.order_type,
                        "quantity": order.quantity,
                        "reason": "price_not_found_in_slab",
                    },
                    db_audit,
                )
                continue

            # Pre-trade 单笔风控（P0-08）：聚合涨跌停板、价格有效性等检查
            pre_trade_reason = self._pre_trade_check(
                order, price, signal_event.trace_id, db_audit, slab=slab
            )
            if pre_trade_reason is not None:
                self._total_skipped += 1
                self._log_audit(
                    "ORDER_SKIPPED",
                    str(uuid.uuid4()),
                    order.trace_id,
                    "Engine",
                    "SKIPPED",
                    {
                        "symbol": order.symbol,
                        "order_type": order.order_type,
                        "quantity": order.quantity,
                        "reason": pre_trade_reason,
                    },
                    db_audit,
                )
                continue

            # 获取当日成交量（用于成交量参与率限制，P0-04）
            daily_volume = self._lookup_price(slab, order.symbol, field="volume") or 0.0

            fill = broker.execute_order(order, price, daily_volume=daily_volume)
            portfolio.update_from_fill(fill)
            if fill.partial_fill:
                self._total_partial_fills += 1

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
                    "partial_fill": fill.partial_fill,
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
        latency_ms: float | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        """记录审计事件。

        审计要求（docs/research/agent-framework-selection-2026.md:40）：
        "每一步状态必须可追溯、可重放"。因此 run_id / latency_ms / timestamp 也写入
        InMemoryAuditTrail，保证内存审计与 DuckDB 审计字段一致。
        """
        run_id = self._current_run_id
        ts = timestamp or datetime.now()
        entry = {
            "run_id": run_id,
            "timestamp": ts,
            "event_type": event_type,
            "trace_id": trace_id,
            "parent_id": parent_id,
            "component": component,
            "status": status,
            "payload": payload,
            "latency_ms": latency_ms,
        }
        try:
            self.audit_logger.log_transition(**entry)
        except Exception:
            logger.warning("InMemoryAuditTrail 写入失败，已降级（审计不阻断主流程）")
        if db_audit:
            try:
                db_audit.log_transition(
                    event_type=event_type,
                    trace_id=trace_id,
                    component=component,
                    status=status,
                    payload=payload,
                    parent_id=parent_id,
                    timestamp=ts,
                    latency_ms=latency_ms,
                )
            except Exception:
                logger.warning("DuckDB 审计写入失败，已降级（审计不阻断主流程）")

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
        metrics_unreliable: bool = False,
    ) -> BacktestResult:
        # 数据可信度门槛：交易日数 / equity_curve 长度不足时拒绝输出指标，
        # 防止把"全程持仓未变 → 零收益"误标为成功的回测结果。
        equity_len = len(portfolio.equity_curve)
        if trading_days < self.MIN_TRADING_DAYS or equity_len < self.MIN_TRADING_DAYS:
            # G5: 样本不足失败必须审计，让上层能从审计日志追溯失败原因
            self._log_audit(
                "INSUFFICIENT_DATA",
                str(uuid.uuid4()),
                self._current_run_id,
                "Engine",
                "FAILED",
                {
                    "trading_days": trading_days,
                    "equity_points": equity_len,
                    "min_required": self.MIN_TRADING_DAYS,
                },
                self._db_audit,
            )
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
            metrics_unreliable=metrics_unreliable,
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
                "benchmark_return": float((bm_arr[-1] / bm_arr[0]) - 1)
                if bm_arr[0] > 0
                else 0.0,
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
        information_ratio = alpha / tracking_error if tracking_error > 0 else 0.0
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
                strategy,
                train_start,
                train_end,
                symbols,
                benchmark_symbol,
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
                failed_folds.append(
                    {
                        "fold_id": fold_idx,
                        "phase": "train",
                        "error_category": train_result.error_category or "unknown",
                        "message": train_result.message,
                    }
                )

            # 测试期回测（重置策略状态和审计日志，防止训练期信息泄漏）
            self.audit_logger.trail.clear()
            strategy.init()
            test_result = self.run(
                strategy,
                test_start,
                test_end,
                symbols,
                benchmark_symbol,
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
                failed_folds.append(
                    {
                        "fold_id": fold_idx,
                        "phase": "test",
                        "error_category": test_result.error_category or "unknown",
                        "message": test_result.message,
                    }
                )

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
                        (pl.col("timestamp") >= start_dt)
                        & (pl.col("timestamp") <= end_dt)
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
            float(np.sqrt(np.mean(downside**2))) * np.sqrt(252)
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
