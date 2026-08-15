"""Dashboard 分析器集成测试

验证 BacktestAnalyzer 从 PostgreSQL 审计库（backtest_audit.logs）
读取数据并生成分析结果。

PG 不可达时整组跳过（Docker 启动后自动恢复运行）。
"""

from uuid import uuid4

import pytest

from long_earn.backtest.engine.audit import AuditLogger, PostgresAuditProvider
from long_earn.core.pg import pg_connect, pg_version


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


def test_analyzer_reads_events_from_db():
    """分析器应能从审计数据库读取事件"""
    run_id = f"run-{uuid4().hex[:10]}"
    provider = PostgresAuditProvider()
    logger = AuditLogger(provider=provider, run_id=run_id)

    # 模拟记录一些事件
    logger.log_transition(
        event_type="SIGNAL",
        trace_id=f"{run_id}-trace-1",
        component="strategy",
        status="SUCCESS",
        payload={"symbol": "AAPL", "weight": 0.5},
    )
    logger.log_transition(
        event_type="FILL",
        trace_id=f"{run_id}-trace-2",
        component="broker",
        status="SUCCESS",
        payload={"symbol": "AAPL", "quantity": 100},
    )
    provider.close()

    # 直查 PG 审计表（避免 backtest_analyzer 中的硬编码 schema 名）
    conn = pg_connect(read_only=True, row_factory=None)
    try:
        events = conn.execute(
            'SELECT * FROM "backtest_audit".logs WHERE run_id = %s',
            [run_id],
        ).fetchall()
    finally:
        conn.close()

    assert len(events) >= 2

    event_types = [r[3] for r in events]
    assert "SIGNAL" in event_types
    assert "FILL" in event_types


def test_analyzer_returns_summary():
    """分析器应返回回测摘要"""
    run_id = f"run-{uuid4().hex[:10]}"
    provider = PostgresAuditProvider()
    logger = AuditLogger(provider=provider, run_id=run_id)

    for i in range(5):
        logger.log_transition(
            event_type="SIGNAL",
            trace_id=f"{run_id}-trace-{i}",
            component="strategy",
            status="SUCCESS",
            payload={"symbol": "AAPL", "weight": 0.2},
        )
    provider.close()

    conn = pg_connect(read_only=True, row_factory=None)
    try:
        summary = conn.execute(
            'SELECT event_type, COUNT(*) as count FROM "backtest_audit".logs '
            "WHERE run_id = %s GROUP BY event_type",
            [run_id],
        ).fetchall()
    finally:
        conn.close()

    assert len(summary) >= 1
    total = sum(r[1] for r in summary)
    assert total == 5
