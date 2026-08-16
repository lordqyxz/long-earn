"""交易数据导出与个股图表可视化接口测试

验证 BacktestAnalyzer 的新增导出/可视化方法能从 PostgreSQL 审计日志
（backtest_audit.logs）正确提取交易记录（时间/标的/金额）与个股价格+
买卖点标注数据。

PG 不可达时整组跳过（Docker 启动后自动恢复运行）。
"""

import json
from datetime import datetime
from uuid import uuid4

import polars as pl
import pytest

from long_earn.app.analyzer import BacktestAnalyzer
from long_earn.backtest.domain.entities import SignalEvent
from long_earn.backtest.engine.audit import (
    RUN_TAG_TEST,
    AuditLogger,
    PostgresAuditProvider,
)
from long_earn.backtest.engine.broker import TradingCostConfig
from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.strategy import BaseStrategy
from long_earn.core.pg import pg_connect, pg_version

# 唯一测试标的：共享 PG 含真实历史行情，固定 symbol（如 600000.SH）会
# 混入真实价格数据；用 UUID 前缀保证本文件写入的测试数据可精确隔离。
_UNIQ = uuid4().hex[:10].upper()
_PRICE_SYM = f"600000.{_UNIQ}"
_OTHER_SYM = f"000001.{_UNIQ}"

# 记录本文件创建的所有 run_id，module 结束时统一清理（保持共享 PG 干净）
_CREATED_RUN_IDS: list[str] = []


def _pg_available() -> bool:
    """探测 PostgreSQL 是否可连（不可达时测试组整体跳过）。"""
    try:
        pg_version()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL 服务不可用",
)


def _make_provider_and_logger(
    run_id: str,
) -> tuple[AuditLogger, PostgresAuditProvider]:
    provider = PostgresAuditProvider()
    logger = AuditLogger(provider=provider, run_id=run_id)
    # 测试回测记录必须携带专用 test 标签（供审计库「清理带 test 标签记录」识别）
    logger.log_run_start({"tags": [RUN_TAG_TEST]})
    return logger, provider


def _write_fills(logger: AuditLogger, run_id: str) -> None:
    """写入模拟 FILL 事件（两笔买入 + 一笔卖出）"""
    logger.log_transition(
        event_type="FILL",
        trace_id=f"{run_id}-t1",
        component="Broker",
        status="SUCCESS",
        payload={
            "symbol": _PRICE_SYM,
            "type": "BUY",
            "price": 10.5,
            "quantity": 1000.0,
            "portfolio_value": 1_000_000.0,
        },
        timestamp=datetime(2023, 1, 3, 9, 30),
    )
    logger.log_transition(
        event_type="FILL",
        trace_id=f"{run_id}-t2",
        component="Broker",
        status="SUCCESS",
        payload={
            "symbol": _OTHER_SYM,
            "type": "BUY",
            "price": 15.2,
            "quantity": 500.0,
            "portfolio_value": 1_005_000.0,
        },
        timestamp=datetime(2023, 1, 4, 9, 30),
    )
    logger.log_transition(
        event_type="FILL",
        trace_id=f"{run_id}-t3",
        component="Broker",
        status="SUCCESS",
        payload={
            "symbol": _PRICE_SYM,
            "type": "SELL",
            "price": 11.8,
            "quantity": 1000.0,
            "portfolio_value": 1_010_000.0,
        },
        timestamp=datetime(2023, 2, 1, 9, 30),
    )


def _write_prices() -> None:
    """写入模拟行情数据到 PG 的 price_daily 表（唯一 symbol，隔离真实数据）"""
    conn = pg_connect()
    try:
        conn.execute(
            "INSERT INTO price_daily VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (symbol, date) DO UPDATE SET close = EXCLUDED.close",
            [_PRICE_SYM, "2023-01-03", 10.3, 10.6, 10.2, 10.5, 10000.0],
        )
        conn.execute(
            "INSERT INTO price_daily VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (symbol, date) DO UPDATE SET close = EXCLUDED.close",
            [_PRICE_SYM, "2023-01-04", 10.6, 10.9, 10.5, 10.8, 12000.0],
        )
        conn.execute(
            "INSERT INTO price_daily VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (symbol, date) DO UPDATE SET close = EXCLUDED.close",
            [_PRICE_SYM, "2023-02-01", 11.5, 11.9, 11.4, 11.8, 8000.0],
        )
        conn.commit()
    finally:
        conn.close()


def _fresh_run_id(prefix: str) -> str:
    """生成隔离 run_id（PG 表全局共享，避免测试间数据污染）并登记清理。"""
    run_id = f"{prefix}-{uuid4().hex[:10]}"
    _CREATED_RUN_IDS.append(run_id)
    return run_id


@pytest.fixture(scope="module", autouse=True)
def _cleanup_module_data():
    """module 结束后清理本文件写入的审计与行情数据（保持共享 PG 干净）。"""
    yield
    conn = pg_connect()
    try:
        for rid in _CREATED_RUN_IDS:
            conn.execute(
                'DELETE FROM "backtest_audit".logs WHERE run_id = %s',
                (rid,),
            )
        conn.execute(
            "DELETE FROM price_daily WHERE symbol IN (%s, %s)",
            (_PRICE_SYM, _OTHER_SYM),
        )
        conn.commit()
    finally:
        conn.close()


def test_export_trade_traces():
    """export_trade_traces 应返回完整交易日志（含金额 = 价格 × 数量）"""
    run_id = _fresh_run_id("run-traces")
    logger, provider = _make_provider_and_logger(run_id)
    _write_fills(logger, run_id)
    provider.close()

    analyzer = BacktestAnalyzer()
    traces = analyzer.export_trade_traces(run_id)

    assert len(traces) == 3
    # 验证字段结构
    first = traces[0]
    assert set(first.keys()) == {
        "time",
        "trace_id",
        "symbol",
        "direction",
        "price",
        "quantity",
        "amount",
        "portfolio_value",
        "reason",
    }
    # 验证金额计算
    assert first["symbol"] == _PRICE_SYM
    assert first["direction"] == "BUY"
    assert first["amount"] == round(10.5 * 1000.0, 2)  # 10500.0
    # 验证方向字段（从 payload.type 映射）
    assert traces[2]["direction"] == "SELL"
    assert traces[2]["amount"] == round(11.8 * 1000.0, 2)  # 11800.0


def test_reason_flows_through_exports():
    """FILL 载荷的 reason 应透传到交易日志/交易明细/个股图表买卖点"""
    run_id = _fresh_run_id("run-reason")
    logger, provider = _make_provider_and_logger(run_id)
    logger.log_transition(
        event_type="FILL",
        trace_id=f"{run_id}-r1",
        component="Broker",
        status="SUCCESS",
        payload={
            "symbol": _PRICE_SYM,
            "type": "BUY",
            "price": 10.5,
            "quantity": 1000.0,
            "portfolio_value": 1_000_000.0,
            "reason": "信号买入·建仓",
        },
        timestamp=datetime(2023, 1, 3, 9, 30),
    )
    logger.log_transition(
        event_type="FILL",
        trace_id=f"{run_id}-r2",
        component="RiskControl",
        status="SUCCESS",
        payload={
            "symbol": _PRICE_SYM,
            "type": "SELL",
            "price": 11.8,
            "quantity": 1000.0,
            "portfolio_value": 1_010_000.0,
            "reason": "止损卖出",
        },
        timestamp=datetime(2023, 2, 1, 9, 30),
    )
    provider.close()
    _write_prices()

    analyzer = BacktestAnalyzer()

    # 交易明细
    journal = analyzer.export_trade_journal(run_id)
    assert [t["reason"] for t in journal] == ["信号买入·建仓", "止损卖出"]

    # 交易日志（含 reason）
    traces = analyzer.export_trade_traces(run_id)
    assert [t["reason"] for t in traces] == ["信号买入·建仓", "止损卖出"]

    # 个股图表买卖点
    chart = analyzer.export_symbol_chart_data(run_id, _PRICE_SYM)
    assert [p["reason"] for p in chart["trade_points"]] == [
        "信号买入·建仓",
        "止损卖出",
    ]


def test_get_traded_symbols():
    """get_traded_symbols 应返回去重后的交易标的列表"""
    run_id = _fresh_run_id("run-sym")
    logger, provider = _make_provider_and_logger(run_id)
    _write_fills(logger, run_id)
    provider.close()

    analyzer = BacktestAnalyzer()
    symbols = analyzer.get_traded_symbols(run_id)

    assert len(symbols) == 2
    assert _PRICE_SYM in symbols
    assert _OTHER_SYM in symbols


def test_export_trade_traces_to_file_csv(tmp_path):
    """export_trade_traces_to_file 应导出 CSV 文件"""
    run_id = _fresh_run_id("run-csv")
    logger, provider = _make_provider_and_logger(run_id)
    _write_fills(logger, run_id)
    provider.close()

    analyzer = BacktestAnalyzer()
    out_path = analyzer.export_trade_traces_to_file(
        run_id, tmp_path / "trades", fmt="csv"
    )
    assert out_path.exists()
    assert out_path.suffix == ".csv"
    content = out_path.read_text(encoding="utf-8")
    assert "symbol" in content
    assert _PRICE_SYM in content
    assert "amount" in content


def test_export_trade_traces_to_file_json(tmp_path):
    """export_trade_traces_to_file 应导出 JSON 文件"""
    run_id = _fresh_run_id("run-json")
    logger, provider = _make_provider_and_logger(run_id)
    _write_fills(logger, run_id)
    provider.close()

    analyzer = BacktestAnalyzer()
    out_path = analyzer.export_trade_traces_to_file(
        run_id, tmp_path / "trades", fmt="json"
    )
    assert out_path.exists()
    assert out_path.suffix == ".json"
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["trade_count"] == 3
    assert len(data["trades"]) == 3
    assert data["trades"][0]["symbol"] == _PRICE_SYM


def test_export_symbol_chart_data():
    """export_symbol_chart_data 应返回价格走势 + 买卖点标注数据"""
    run_id = _fresh_run_id("run-chart")
    logger, provider = _make_provider_and_logger(run_id)
    _write_fills(logger, run_id)
    provider.close()
    _write_prices()

    analyzer = BacktestAnalyzer()
    chart = analyzer.export_symbol_chart_data(run_id, _PRICE_SYM)

    assert chart["symbol"] == _PRICE_SYM
    # 价格历史应有 3 个交易日（唯一 symbol，无真实数据混入）
    assert len(chart["price_history"]) == 3
    assert chart["price_history"][0]["close"] == 10.5
    # 交易点应有 2 个（1 买 + 1 卖）
    assert len(chart["trade_points"]) == 2
    buy = chart["trade_points"][0]
    assert buy["direction"] == "BUY"
    assert buy["price"] == 10.5
    assert buy["quantity"] == 1000.0
    assert buy["amount"] == round(10.5 * 1000.0, 2)
    sell = chart["trade_points"][1]
    assert sell["direction"] == "SELL"
    assert sell["price"] == 11.8


def test_export_all_symbol_charts():
    """export_all_symbol_charts 应为每个交易标的生成图表数据"""
    run_id = _fresh_run_id("run-all")
    logger, provider = _make_provider_and_logger(run_id)
    _write_fills(logger, run_id)
    provider.close()
    _write_prices()

    analyzer = BacktestAnalyzer()
    charts = analyzer.export_all_symbol_charts(run_id)

    assert len(charts) == 2
    symbols = [c["symbol"] for c in charts]
    assert _PRICE_SYM in symbols
    assert _OTHER_SYM in symbols
    # _PRICE_SYM 有价格数据
    chart_price = next(c for c in charts if c["symbol"] == _PRICE_SYM)
    assert len(chart_price["price_history"]) == 3
    assert len(chart_price["trade_points"]) == 2


def test_export_dashboard_data_includes_traded_symbols():
    """export_dashboard_data 应包含 traded_symbols 字段"""
    run_id = _fresh_run_id("run-dash")
    logger, provider = _make_provider_and_logger(run_id)
    _write_fills(logger, run_id)
    provider.close()

    analyzer = BacktestAnalyzer()
    data = analyzer.export_dashboard_data(run_id)

    assert "traded_symbols" in data
    assert len(data["traded_symbols"]) == 2
    assert _PRICE_SYM in data["traded_symbols"]


def _write_attribution_chain(logger: AuditLogger, run_id: str) -> None:
    """写入一条信号单链（SIGNAL→ORDER→FILL）和一条风控单链（RISK_TRIGGER→ORDER→FILL）。"""
    sig_trace = f"{run_id}-sig"
    ord_trace = f"{run_id}-ord"
    fill_trace = f"{run_id}-fill"
    logger.log_transition(
        event_type="SIGNAL",
        trace_id=sig_trace,
        component="Strategy",
        status="SUCCESS",
        payload={
            "signals": {"A": 0.5, "B": 0.5},
            "strategy_id": "test-mom",
            "risk_triggered": False,
            "rationale": {
                "formula": "mom = close 的 20 期收益率；筛选 mom > 0；按 mom 降序取前 2；等权",
                "criteria": [
                    {
                        "step": "factor",
                        "op": "returns",
                        "alias": "mom",
                        "format": "pct",
                        "desc": "mom = close 的 20 期收益率",
                        "params": {"field": "close", "period": 20},
                    },
                    {
                        "step": "signal",
                        "op": "rank_top",
                        "desc": "按 mom 降序取前 2",
                        "params": {"field": "mom", "top": 2, "ascending": False},
                    },
                ],
                "selection": [
                    {"symbol": "A", "rank": 1, "mom": 0.5},
                    {"symbol": "B", "rank": 2, "mom": 0.4},
                ],
                "universe_size": 10,
                "selected_count": 2,
                "weights": {"method": "equal"},
            },
        },
        timestamp=datetime(2023, 1, 3, 9, 30),
    )
    logger.log_transition(
        event_type="ORDER",
        trace_id=ord_trace,
        parent_id=sig_trace,
        component="Portfolio",
        status="SUCCESS",
        payload={"symbol": "A", "type": "BUY", "quantity": 500.0},
        timestamp=datetime(2023, 1, 3, 9, 31),
    )
    logger.log_transition(
        event_type="FILL",
        trace_id=fill_trace,
        parent_id=ord_trace,
        component="Broker",
        status="SUCCESS",
        payload={
            "symbol": "A",
            "type": "BUY",
            "price": 10.0,
            "quantity": 500.0,
            "reason": "信号买入·建仓（目标权重50%）",
            "portfolio_value": 1_000_000.0,
        },
        timestamp=datetime(2023, 1, 3, 9, 32),
    )
    risk_trace = f"{run_id}-risk"
    ord2_trace = f"{run_id}-ord2"
    fill2_trace = f"{run_id}-fill2"
    logger.log_transition(
        event_type="RISK_TRIGGER",
        trace_id=risk_trace,
        component="RiskControl",
        status="WARNING",
        payload={
            "risk_type": "stop_loss",
            "symbol": "A",
            "avg_cost": 10.0,
            "check_price": 8.8,
            "pnl_pct": -0.12,
            "stop_loss_threshold": 0.1,
            "quantity": 500.0,
            "timestamp": "2023-02-01 00:00:00",
        },
        timestamp=datetime(2023, 2, 1, 9, 30),
    )
    logger.log_transition(
        event_type="ORDER",
        trace_id=ord2_trace,
        parent_id=risk_trace,
        component="RiskControl",
        status="SUCCESS",
        payload={"symbol": "A", "type": "SELL", "quantity": 500.0},
        timestamp=datetime(2023, 2, 1, 9, 31),
    )
    logger.log_transition(
        event_type="FILL",
        trace_id=fill2_trace,
        parent_id=ord2_trace,
        component="RiskControl",
        status="SUCCESS",
        payload={
            "symbol": "A",
            "type": "SELL",
            "price": 8.8,
            "quantity": 500.0,
            "reason": "止损卖出（跌幅12.0%，成本10.00→触发8.80，止损线-10%）",
            "portfolio_value": 995_000.0,
        },
        timestamp=datetime(2023, 2, 1, 9, 32),
    )


def test_trade_journal_attribution_reconstructs_chain():
    """export_trade_journal 应还原每笔 FILL 的审计归因链（信号单与风控单）。"""
    run_id = _fresh_run_id("run-attr")
    logger, provider = _make_provider_and_logger(run_id)
    _write_attribution_chain(logger, run_id)
    provider.close()

    analyzer = BacktestAnalyzer()
    journal = analyzer.export_trade_journal(run_id)
    assert len(journal) == 2

    signal_trade = next(t for t in journal if t["type"] == "BUY")
    assert signal_trade["attribution"]["kind"] == "signal"
    assert signal_trade["attribution"]["signal"]["strategy_id"] == "test-mom"
    assert signal_trade["attribution"]["signal"]["signals"] == {"A": 0.5, "B": 0.5}
    assert signal_trade["attribution"]["order"]["quantity"] == 500.0
    # 决策依据（因子公式 + 选股因子值）透传到归因
    rationale = signal_trade["attribution"]["signal"]["rationale"]
    assert rationale["formula"]
    assert rationale["selection"][0]["rank"] == 1
    assert rationale["selection"][0]["symbol"] == "A"
    assert rationale["criteria"][0]["format"] == "pct"

    risk_trade = next(t for t in journal if t["type"] == "SELL")
    assert risk_trade["attribution"]["kind"] == "risk"
    assert risk_trade["attribution"]["risk_trigger"]["risk_type"] == "stop_loss"
    assert risk_trade["attribution"]["risk_trigger"]["pnl_pct"] == -0.12
    assert risk_trade["attribution"]["chain"]["upstream"]

    # 链上每个节点带紧凑事件摘要（hover 展示用）：upstream/order/fill 三节点齐全
    events = signal_trade["attribution"]["chain"]["events"]
    assert set(events) == {"upstream", "order", "fill"}
    assert events["fill"]["event_type"] == "FILL"
    assert "BUY" in events["fill"]["summary"] and "A" in events["fill"]["summary"]
    assert events["order"]["event_type"] == "ORDER"
    assert (
        "策略" in events["upstream"]["summary"]
        and "选股" in events["upstream"]["summary"]
    )
    risk_events = risk_trade["attribution"]["chain"]["events"]
    assert "止损触发" in risk_events["upstream"]["summary"]

    # 按 trace_id 下钻原始事件记录（完整 payload，供点击核验）
    drill = analyzer.export_audit_event(
        run_id, signal_trade["attribution"]["chain"]["fill"]
    )
    assert len(drill) == 1
    assert drill[0]["event_type"] == "FILL"
    assert drill[0]["payload"]["symbol"] == "A"
    assert "status" in drill[0] and "timestamp" in drill[0]
    assert analyzer.export_audit_event(run_id, "no-such-trace") == []


def test_engine_risk_fill_enriched_reason_and_chain():
    """引擎端到端：风控 FILL 原因含触发数值，且 FILL→ORDER→RISK_TRIGGER 归因链可还原。"""
    provider = PostgresAuditProvider()

    rows = []
    for i, close in enumerate([10.0, 10.0, 9.1, 8.6, 8.3]):
        ts = datetime(2024, 2, i + 1)
        rows.append(
            {
                "timestamp": ts,
                "symbol": "A.SZ",
                "open": 10.0 if i < 2 else close,
                "high": max(close, 10.0) * 1.01,
                "low": close * 0.97,
                "close": close,
                "volume": 100000.0,
            }
        )
    panel = pl.DataFrame(rows)

    class _MockProvider:
        def __init__(self, p):
            self._panel = p

        def get_merged_panel_as_polars(self, symbols, start, end):
            return self._panel.filter(
                (pl.col("symbol").is_in(symbols))
                & (pl.col("timestamp") >= datetime.strptime(start, "%Y-%m-%d"))
                & (pl.col("timestamp") <= datetime.strptime(end, "%Y-%m-%d"))
            )

    class _BuyOnce(BaseStrategy):
        def __init__(self):
            super().__init__(strategy_id="test-eng")
            self._called = False

        def init(self):
            self._called = False

        def on_bar(self, bars, context):
            if self._called:
                return None
            self._called = True
            ts = bars.select("timestamp").to_series()[0]
            return SignalEvent(
                timestamp=ts,
                trace_id="sig-eng",
                event_id="sig-eng-ev",
                signals={"A.SZ": 1.0},
                strategy_id="test-eng",
                metadata={
                    "rationale": {
                        "formula": "mom = close 的 2 期收益率；按 mom 降序取前 1；等权",
                        "criteria": [
                            {
                                "step": "factor",
                                "op": "returns",
                                "alias": "mom",
                                "format": "pct",
                                "desc": "mom = close 的 2 期收益率",
                                "params": {"field": "close", "period": 2},
                            }
                        ],
                        "selection": [{"symbol": "A.SZ", "rank": 1, "mom": 0.1}],
                        "universe_size": 1,
                        "selected_count": 1,
                        "weights": {"method": "equal"},
                    }
                },
            )

    engine = EventDrivenBacktestEngine(
        data_provider=_MockProvider(panel),
        cost_config=TradingCostConfig(),
        audit_provider=provider,
        stop_loss=0.1,
        max_drawdown_limit=0.3,
    )
    engine.run(_BuyOnce(), "2024-02-01", "2024-02-05", ["A.SZ"], tags=[RUN_TAG_TEST])
    engine_run_id = engine._current_run_id
    _CREATED_RUN_IDS.append(engine_run_id)
    provider.close()

    # 引擎内存轨迹：信号单带目标权重，风控单带触发数值
    trail = engine.audit_logger.get_full_trail()
    fill_reasons = [
        e["payload"].get("reason") for e in trail if e.get("event_type") == "FILL"
    ]
    assert any("目标权重" in r for r in fill_reasons)
    assert any("止损卖出（跌幅" in r and "止损线" in r for r in fill_reasons)

    # analyzer 归因链还原
    analyzer = BacktestAnalyzer()
    journal = analyzer.export_trade_journal(engine_run_id)
    kinds = {t["attribution"]["kind"] for t in journal}
    assert kinds == {"signal", "risk"}
    risk_trade = next(t for t in journal if t["attribution"]["kind"] == "risk")
    assert risk_trade["attribution"]["risk_trigger"]["risk_type"] == "stop_loss"
    # 引擎把 SignalEvent.metadata["rationale"] 透传到 SIGNAL 审计 → 归因
    signal_trade = next(t for t in journal if t["attribution"]["kind"] == "signal")
    rationale = signal_trade["attribution"]["signal"]["rationale"]
    assert rationale and rationale["formula"]
    assert rationale["selection"][0]["symbol"] == "A.SZ"
