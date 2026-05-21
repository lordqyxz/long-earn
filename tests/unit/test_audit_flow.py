import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from long_earn.backtest.domain.entities import SignalEvent
from long_earn.backtest.engine.audit import DuckDBAuditProvider
from long_earn.backtest.engine.broker import TradingCostConfig
from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.strategy import BaseStrategy
from long_earn.tools.backtest_analyzer import BacktestAnalyzer


class MockStrategy(BaseStrategy):
    def init(self):
        self._state = {"initialized": True}

    def on_bar(self, slab: pl.DataFrame, context: Any) -> SignalEvent | None:
        return SignalEvent(
            timestamp=datetime.now(),
            trace_id=str(uuid.uuid4()),
            event_id=f"sig_{uuid.uuid4().hex[:6]}",
            signals={"AAPL": 0.1},
            strategy_id="test_strat",
        )


def test_audit_causal_flow():
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "test_audit.duckdb"

    try:
        provider = DuckDBAuditProvider(db_path=db_path)
        engine = EventDrivenBacktestEngine(
            audit_provider=provider, cost_config=TradingCostConfig()
        )

        full_data = pl.DataFrame(
            {"timestamp": [datetime.now()], "symbol": ["AAPL"], "close": [150.0]}
        )

        engine._prepare_data = lambda s, start, end: full_data

        strategy = MockStrategy(strategy_id="test_strat")
        engine.run(strategy, "2023-01-01", "2023-01-02", ["AAPL"])

        analyzer = BacktestAnalyzer(db_path=db_path)

        all_events = analyzer.run_custom_query(
            "SELECT * FROM audit.logs ORDER BY timestamp ASC"
        )
        assert not all_events.is_empty(), "No audit events were recorded!"

        signals = all_events.filter(pl.col("event_type") == "SIGNAL")
        assert not signals.is_empty(), "No SIGNAL event found in audit logs!"

        target_trace_id = signals["trace_id"][0]
        chain = analyzer.trace_trade_lifecycle(target_trace_id)

        event_types = chain["event_type"].to_list()
        trace_ids = chain["trace_id"].to_list()
        expected = ["MARKET_DATA", "SIGNAL", "ORDER", "FILL"]

        for e in expected:
            assert e in event_types, f"Missing event type {e} in causal chain!"

        assert len(set(trace_ids)) == 4, (
            f"Expected 4 distinct trace_ids, got {len(set(trace_ids))}"
        )

        fills = chain.filter(pl.col("event_type") == "FILL")
        orders = chain.filter(pl.col("event_type") == "ORDER")
        signal_events = chain.filter(pl.col("event_type") == "SIGNAL")

        assert fills["parent_id"][0] == orders["trace_id"][0]
        assert orders["parent_id"][0] == signal_events["trace_id"][0]

    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)
