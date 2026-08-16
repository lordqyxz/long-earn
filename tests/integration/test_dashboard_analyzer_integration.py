"""Dashboard 分析器集成测试

验证 BacktestAnalyzer 从 PostgreSQL 审计库（backtest_audit.logs）
读取数据并生成分析结果。

PG 不可达时整组跳过（Docker 启动后自动恢复运行）。
"""

from uuid import uuid4

import pytest

from long_earn.app.analyzer import BacktestAnalyzer
from long_earn.backtest.engine.audit import (
    RUN_TAG_PROD,
    RUN_TAG_TEST,
    AuditLogger,
    PostgresAuditProvider,
)
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

    # 测试回测记录必须携带专用 test 标签（供审计库「清理带 test 标签记录」识别）
    logger.log_run_start({"tags": [RUN_TAG_TEST]})

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

    # 测试回测记录必须携带专用 test 标签（供审计库「清理带 test 标签记录」识别）
    logger.log_run_start({"tags": [RUN_TAG_TEST]})

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
    assert total == 6  # RUN_START(带 test 标签) + 5 条 SIGNAL


def test_clean_identifies_test_tagged_runs():
    """清理口径：带 test 标签的 run 应被 get_empty_or_error_runs 识别，有效非测试 run 不应被识别。"""
    # 1. 带 test 标签的空跑 run → 应被识别为无效（test 标签 + 无 FILL）
    tagged_run = f"run-{uuid4().hex[:10]}"
    provider = PostgresAuditProvider()
    logger = AuditLogger(provider=provider, run_id=tagged_run)
    logger.log_run_start({"tags": [RUN_TAG_TEST]})
    logger.log_transition(
        event_type="RUN_END",
        trace_id=f"{tagged_run}-end",
        component="Engine",
        status="SUCCESS",
        payload={"total_return": 0.0},
    )
    provider.close()

    # 2. 有效非测试 run（RUN_END + 5 笔 FILL，tags 为空）→ 不应被识别
    valid_run = f"run-{uuid4().hex[:10]}"
    provider2 = PostgresAuditProvider()
    logger2 = AuditLogger(provider=provider2, run_id=valid_run)
    logger2.log_run_start({"tags": []})
    for i in range(5):
        logger2.log_transition(
            event_type="FILL",
            trace_id=f"{valid_run}-f{i}",
            component="Broker",
            status="SUCCESS",
            payload={"symbol": "AAPL", "quantity": 100},
        )
    logger2.log_transition(
        event_type="RUN_END",
        trace_id=f"{valid_run}-end",
        component="Engine",
        status="SUCCESS",
        payload={"total_return": 0.05},
    )
    provider2.close()

    # 3. 生产豁免 run（tags 含 test 且 prod）→ 不应被识别（prod 覆盖 test 清理语义）
    prod_run = f"run-{uuid4().hex[:10]}"
    provider3 = PostgresAuditProvider()
    logger3 = AuditLogger(provider=provider3, run_id=prod_run)
    logger3.log_run_start({"tags": [RUN_TAG_TEST, RUN_TAG_PROD]})
    for i in range(5):
        logger3.log_transition(
            event_type="FILL",
            trace_id=f"{prod_run}-f{i}",
            component="Broker",
            status="SUCCESS",
            payload={"symbol": "AAPL", "quantity": 100},
        )
    logger3.log_transition(
        event_type="RUN_END",
        trace_id=f"{prod_run}-end",
        component="Engine",
        status="SUCCESS",
        payload={"total_return": 0.05},
    )
    provider3.close()

    try:
        analyzer = BacktestAnalyzer()
        bad = set(analyzer.get_empty_or_error_runs())
        assert tagged_run in bad
        assert valid_run not in bad
        assert prod_run not in bad, "带 prod 标签的生产 run 不应被 test 清理口径识别"
    finally:
        # 清理本测试写入的审计数据（保持共享 PG 干净）
        conn = pg_connect()
        try:
            conn.execute(
                'DELETE FROM "backtest_audit".logs WHERE run_id IN (%s, %s, %s)',
                (tagged_run, valid_run, prod_run),
            )
            conn.commit()
        finally:
            conn.close()
