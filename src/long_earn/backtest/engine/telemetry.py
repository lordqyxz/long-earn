"""OpenTelemetry 集成模块

为回测引擎提供可观测性：trace 事件流和 audit 日志。
"""

from typing import Any

from long_earn.backtest.domain.entities import (
    FillEvent,
    MarketDataEvent,
    OrderEvent,
    SignalEvent,
)
from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.portfolio import Portfolio


class OtelSpanContext:
    """轻量级 trace 上下文模拟

    无需 OpenTelemetry SDK 依赖即可记录结构化的 span 链路，
    后续可切换为真正的 OpenTelemetry tracer。
    """

    def __init__(self):
        self.spans: list[dict[str, Any]] = []

    def start_span(self, name: str, attributes: dict[str, Any] | None = None) -> int:
        span_id = len(self.spans)
        self.spans.append(
            {
                "name": name,
                "span_id": span_id,
                "parent_id": None,
                "attributes": attributes or {},
                "events": [],
            }
        )
        return span_id

    def end_span(self, span_id: int) -> None:
        if span_id < len(self.spans):
            self.spans[span_id]["ended"] = True

    def add_event(
        self, span_id: int, name: str, attributes: dict[str, Any] | None = None
    ) -> None:
        if span_id < len(self.spans):
            self.spans[span_id]["events"].append(
                {
                    "name": name,
                    "attributes": attributes or {},
                }
            )

    def set_parent(self, child_id: int, parent_id: int) -> None:
        if child_id < len(self.spans) and parent_id < len(self.spans):
            self.spans[child_id]["parent_id"] = parent_id

    def get_trace(self) -> list[dict[str, Any]]:
        return self.spans

    def to_dict(self) -> dict[str, Any]:
        return {
            "spans": self.spans,
            "span_count": len(self.spans),
        }


def instrument_engine(engine: EventDrivenBacktestEngine) -> OtelSpanContext:
    """为引擎注入 OpenTelemetry trace 上下文

    在 run() 调用前执行，返回 trace 上下文。
    引擎执行完成后可调用 ctx.get_trace() 获取完整 span 链路。
    """
    ctx = OtelSpanContext()
    engine._otel_ctx = ctx  # type: ignore[attr-defined]
    return ctx


def _before_bar_loop(engine: EventDrivenBacktestEngine) -> None:
    """Hook: bar 循环开始前"""
    ctx = getattr(engine, "_otel_ctx", None)
    if ctx is not None:
        ctx.start_span("backtest_run", {"strategy": str(type(engine))})


def _on_market_data(
    ctx: OtelSpanContext, event: MarketDataEvent, portfolio: Portfolio
) -> int:
    """记录 MarketDataEvent 为 span"""
    sid = ctx.start_span(
        f"bar_{event.timestamp}",
        {
            "timestamp": str(event.timestamp),
            "portfolio_value": portfolio.total_value,
            "trace_id": event.trace_id,
        },
    )
    return sid


def _on_signal(
    ctx: OtelSpanContext,
    parent_span: int,
    event: SignalEvent,
) -> int:
    """记录 SignalEvent 为 span"""
    sid = ctx.start_span(
        "signal",
        {
            "strategy_id": event.strategy_id,
            "signals": str(event.signals),
            "trace_id": event.trace_id,
        },
    )
    ctx.set_parent(sid, parent_span)
    return sid


def _on_order(ctx: OtelSpanContext, parent_span: int, event: OrderEvent) -> int:
    """记录 OrderEvent 为 span"""
    sid = ctx.start_span(
        "order",
        {
            "symbol": event.symbol,
            "type": event.order_type,
            "quantity": event.quantity,
            "trace_id": event.trace_id,
        },
    )
    ctx.set_parent(sid, parent_span)
    return sid


def _on_fill(ctx: OtelSpanContext, parent_span: int, event: FillEvent) -> int:
    """记录 FillEvent 为 span"""
    sid = ctx.start_span(
        "fill",
        {
            "symbol": event.symbol,
            "type": event.order_type,
            "price": event.fill_price,
            "quantity": event.fill_quantity,
            "commission": event.commission,
            "trace_id": event.trace_id,
        },
    )
    ctx.set_parent(sid, parent_span)
    return sid
