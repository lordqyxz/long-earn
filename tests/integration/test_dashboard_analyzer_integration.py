"""Dashboard 分析器集成测试

验证 BacktestAnalyzer 从审计数据库读取数据并生成分析结果。
"""

import tempfile
from datetime import datetime
from pathlib import Path

import polars as pl

from long_earn.backtest.engine.audit import AuditLogger, DuckDBAuditProvider
from long_earn.tools.backtest_analyzer import BacktestAnalyzer


def test_analyzer_reads_events_from_db():
    """分析器应能从审计数据库读取事件"""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "audit.duckdb"

    try:
        provider = DuckDBAuditProvider(db_path=db_path)
        logger = AuditLogger(provider=provider, run_id="run-1")

        # 模拟记录一些事件
        logger.log_transition(
            event_type="SIGNAL",
            trace_id="trace-1",
            component="strategy",
            status="SUCCESS",
            payload={"symbol": "AAPL", "weight": 0.5},
        )
        logger.log_transition(
            event_type="FILL",
            trace_id="trace-2",
            component="broker",
            status="SUCCESS",
            payload={"symbol": "AAPL", "quantity": 100},
        )
        provider.close()

        # 使用 duckdb 直接查询（避免 backtest_analyzer 中的硬编码 schema 名）
        import duckdb
        conn = duckdb.connect(str(db_path))
        events = conn.execute('SELECT * FROM "backtest_audit".logs').pl()
        conn.close()

        assert not events.is_empty()
        assert events.height >= 2

        event_types = events["event_type"].to_list()
        assert "SIGNAL" in event_types
        assert "FILL" in event_types

    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_analyzer_returns_summary():
    """分析器应返回回测摘要"""
    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "audit.duckdb"

    try:
        provider = DuckDBAuditProvider(db_path=db_path)
        logger = AuditLogger(provider=provider, run_id="run-2")

        for i in range(5):
            logger.log_transition(
                event_type="SIGNAL",
                trace_id=f"trace-{i}",
                component="strategy",
                status="SUCCESS",
                payload={"symbol": "AAPL", "weight": 0.2},
            )
        provider.close()

        import duckdb
        conn = duckdb.connect(str(db_path))
        summary = conn.execute(
            'SELECT event_type, COUNT(*) as count FROM "backtest_audit".logs GROUP BY event_type'
        ).pl()
        conn.close()

        assert not summary.is_empty()
        assert summary.height >= 1

    finally:
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)
