"""审计流接口测试

验证 DuckDBAuditProvider + BacktestAnalyzer 的集成接口，
不验证内部因果链细节。
"""

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


def test_audit_records_events():
    """审计系统应记录引擎执行事件"""
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

        # 使用 duckdb 直接查询（避免 backtest_analyzer 中的硬编码 schema 名）
        import duckdb
        conn = duckdb.connect(str(db_path))
        all_events = conn.execute(
            'SELECT * FROM "backtest_audit".logs ORDER BY timestamp ASC'
        ).pl()
        conn.close()
        assert not all_events.is_empty(), "No audit events were recorded!"

        event_types = all_events["event_type"].to_list()
        assert "SIGNAL" in event_types, "No SIGNAL event found in audit logs!"

    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)
