"""PostgreSQL 审计 Provider 安全测试

覆盖：
- P2-13：query_events key 白名单校验（防 SQL 注入）
- P2-14：单连接线程安全（并发写不崩溃）
- P1-10：seq 自增序列号保证单调排序（墙钟回退不破坏因果链）

PostgreSQL 不可达时整组跳过（Docker 启动后自动恢复运行）。
"""

import threading
from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest

from long_earn.backtest.domain.interfaces import AuditRecord
from long_earn.backtest.engine.audit import PostgresAuditProvider
from long_earn.core.pg import pg_version


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


def _make_record(
    run_id: str = "test-run",
    trace_id: str = "trace-0",
    event_type: str = "MARKET_DATA",
    parent_id: str | None = None,
) -> AuditRecord:
    """构造测试用审计记录"""
    return AuditRecord(
        run_id=run_id,
        timestamp=datetime.now(),
        event_type=event_type,
        trace_id=trace_id,
        parent_id=parent_id,
        component="engine",
        status="OK",
        payload={"bar": "2024-01-01"},
        latency_ms=0.5,
    )


@pytest.fixture
def provider() -> Any:
    """为每个测试提供独立的 PostgresAuditProvider + 隔离 run_id。

    使用唯一 run_id 隔离测试数据（PG 表全局共享，不能互相污染）。
    """
    run_id = f"t-{uuid4().hex[:12]}"
    prov = PostgresAuditProvider()
    prov.log_event(_make_record(run_id=run_id))
    yield (prov, run_id)
    prov.close()


class TestQueryEventsWhitelist:
    """P2-13：query_events key 白名单校验"""

    def test_whitelisted_key_accepted(self, provider) -> None:
        """白名单内的字段应正常过滤"""
        prov, run_id = provider
        records = prov.query_events(run_id, {"event_type": "MARKET_DATA"})
        assert len(records) == 1
        assert records[0].event_type == "MARKET_DATA"

    def test_non_whitelisted_key_rejected(self, provider) -> None:
        """非白名单 key 应抛 ValueError，不拼接到 SQL"""
        prov, _ = provider
        with pytest.raises(ValueError):
            prov.query_events("x", {"1=1 OR 1": "x"})
        with pytest.raises(ValueError) as ctx:
            prov.query_events("x", {"1=1 OR 1": "x"})
        assert "非白名单" in str(ctx.value)

    def test_sql_injection_attempt_rejected(self, provider) -> None:
        """典型 SQL 注入 payload 应被白名单拒绝"""
        prov, _ = provider
        with pytest.raises(ValueError):
            prov.query_events(
                "x", {"event_type; DROP TABLE logs--": "x"}
            )

    def test_empty_filters_returns_all(self, provider) -> None:
        """空 filters 应返回该 run_id 全部记录"""
        prov, run_id = provider
        records = prov.query_events(run_id, {})
        assert len(records) == 1


class TestThreadSafety:
    """P2-14：单连接线程安全（锁保护）"""

    def test_concurrent_log_event_no_crash(self) -> None:
        """多线程并发 log_event 不应崩溃（锁串行化）"""
        run_id = f"conc-{uuid4().hex[:12]}"
        provider = PostgresAuditProvider()
        errors: list[Exception] = []

        def worker(thread_id: int) -> None:
            try:
                for i in range(20):
                    provider.log_event(
                        _make_record(
                            run_id=run_id,
                            trace_id=f"trace-{thread_id}-{i}",
                        )
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        provider.close()

        assert errors == [], f"并发写入产生异常: {errors}"
        provider2 = PostgresAuditProvider()
        for t in range(4):
            records = provider2.query_events(run_id, {"trace_id": f"trace-{t}-0"})
            assert len(records) == 1, f"thread-{t} 记录数不完整"
        provider2.close()

    def test_concurrent_query_event_no_crash(self) -> None:
        """多线程并发读 + 写不应崩溃"""
        run_id = f"rw-{uuid4().hex[:12]}"
        provider = PostgresAuditProvider()
        for i in range(10):
            provider.log_event(_make_record(run_id=run_id, trace_id=f"trace-{i}"))

        errors: list[Exception] = []

        def reader() -> None:
            try:
                for _ in range(20):
                    provider.query_events(run_id, {})
            except Exception as e:
                errors.append(e)

        def writer() -> None:
            try:
                for i in range(20):
                    provider.log_event(
                        _make_record(run_id=run_id, trace_id=f"new-trace-{i}")
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(3)] + [
            threading.Thread(target=writer) for _ in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        provider.close()
        assert errors == [], f"并发读写产生异常: {errors}"


class TestSeqMonotonicity:
    """P1-10：seq 自增序列号保证因果排序（墙钟回退不破坏因果链）"""

    def test_causal_chain_ordered_by_seq_not_timestamp(self) -> None:
        """get_causal_chain 应按 seq 排序，而非 timestamp（墙钟回退场景）"""
        trace_id = f"chain-{uuid4().hex[:12]}"
        provider = PostgresAuditProvider()

        base = datetime(2026, 1, 1, 10, 0, 0)
        records = [
            _make_record(trace_id=trace_id, event_type="MARKET_DATA"),
            _make_record(trace_id=trace_id, event_type="SIGNAL"),
            _make_record(trace_id=trace_id, event_type="ORDER"),
        ]
        records[0].timestamp = base
        records[1].timestamp = base
        records[2].timestamp = datetime(2025, 12, 31, 9, 0, 0)  # 回退！

        for r in records:
            provider.log_event(r)

        chain = provider.get_causal_chain(trace_id)
        provider.close()

        assert len(chain) == 3
        assert chain[0].event_type == "MARKET_DATA"
        assert chain[1].event_type == "SIGNAL"
        assert chain[2].event_type == "ORDER"

    def test_duplicate_timestamp_does_not_clobber(self) -> None:
        """相同 timestamp 的两条记录不应因主键冲突而互相覆盖（seq 区分）"""
        trace_id = f"dup-{uuid4().hex[:12]}"
        provider = PostgresAuditProvider()

        ts = datetime(2026, 1, 1, 10, 0, 0)
        r1 = _make_record(trace_id=trace_id, event_type="MARKET_DATA")
        r1.timestamp = ts
        r2 = _make_record(trace_id=trace_id, event_type="SIGNAL")
        r2.timestamp = ts

        provider.log_event(r1)
        provider.log_event(r2)

        chain = provider.get_causal_chain(trace_id)
        provider.close()

        assert len(chain) == 2, "相同 timestamp 记录不应被覆盖"
