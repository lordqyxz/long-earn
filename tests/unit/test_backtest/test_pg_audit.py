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

import psycopg
import pytest

from long_earn.backtest.domain.interfaces import AuditRecord
from long_earn.backtest.engine.audit import RUN_TAG_TEST, PostgresAuditProvider
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


def _log_run_start(prov: PostgresAuditProvider, run_id: str) -> None:
    """记录 RUN_START 事件并携带专用 test 标签（供审计库「清理带 test 标签记录」识别）。"""
    prov.log_event(
        AuditRecord(
            run_id=run_id,
            timestamp=datetime.now(),
            event_type="RUN_START",
            trace_id=run_id,
            parent_id=None,
            component="Engine",
            status="SUCCESS",
            payload={"tags": [RUN_TAG_TEST]},
            latency_ms=0.0,
        )
    )


@pytest.fixture
def provider() -> Any:
    """为每个测试提供独立的 PostgresAuditProvider + 隔离 run_id。

    使用唯一 run_id 隔离测试数据（PG 表全局共享，不能互相污染）。
    """
    run_id = f"t-{uuid4().hex[:12]}"
    prov = PostgresAuditProvider()
    prov.log_event(_make_record(run_id=run_id))
    _log_run_start(prov, run_id)
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
            prov.query_events("x", {"event_type; DROP TABLE logs--": "x"})

    def test_empty_filters_returns_all(self, provider) -> None:
        """空 filters 应返回该 run_id 全部记录"""
        prov, run_id = provider
        records = prov.query_events(run_id, {})
        assert len(records) == 2  # MARKET_DATA + RUN_START(带 test 标签)


class TestThreadSafety:
    """P2-14：单连接线程安全（锁保护）"""

    def test_concurrent_log_event_no_crash(self) -> None:
        """多线程并发 log_event 不应崩溃（锁串行化）"""
        run_id = f"conc-{uuid4().hex[:12]}"
        provider = PostgresAuditProvider()
        _log_run_start(provider, run_id)
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
        _log_run_start(provider, run_id)
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
        run_id = f"seq-{uuid4().hex[:12]}"
        trace_id = f"chain-{uuid4().hex[:12]}"
        provider = PostgresAuditProvider()
        _log_run_start(provider, run_id)

        base = datetime(2026, 1, 1, 10, 0, 0)
        records = [
            _make_record(run_id=run_id, trace_id=trace_id, event_type="MARKET_DATA"),
            _make_record(run_id=run_id, trace_id=trace_id, event_type="SIGNAL"),
            _make_record(run_id=run_id, trace_id=trace_id, event_type="ORDER"),
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
        run_id = f"dup2-{uuid4().hex[:12]}"
        trace_id = f"dup-{uuid4().hex[:12]}"
        provider = PostgresAuditProvider()
        _log_run_start(provider, run_id)

        ts = datetime(2026, 1, 1, 10, 0, 0)
        r1 = _make_record(run_id=run_id, trace_id=trace_id, event_type="MARKET_DATA")
        r1.timestamp = ts
        r2 = _make_record(run_id=run_id, trace_id=trace_id, event_type="SIGNAL")
        r2.timestamp = ts

        provider.log_event(r1)
        provider.log_event(r2)

        chain = provider.get_causal_chain(trace_id)
        provider.close()

        assert len(chain) == 2, "相同 timestamp 记录不应被覆盖"


class TestSelfHealing:
    """连接毒化自愈契约

    psycopg3 的隐式事务在语句失败后进入 aborted 状态，之后所有语句都抛
    InFailedSqlTransaction 且永不自愈；连接级故障则需丢弃连接待重连。
    Provider 必须在失败后自愈，否则一次瞬时错误会毒化整条审计链路。
    """

    def test_nan_payload_write_ok(self) -> None:
        """NaN/Inf payload 应被 sanitize 为 null 后正常写入。

        json.dumps 默认输出 NaN/Infinity 字面量（非合法 JSON），PG jsonb
        列会拒绝——这是审计连接被毒化的已知第一因。
        """
        run_id = f"nan-{uuid4().hex[:12]}"
        provider = PostgresAuditProvider()
        _log_run_start(provider, run_id)
        rec = _make_record(run_id=run_id)
        rec.payload = {
            "sharpe": float("nan"),
            "inf": float("inf"),
            "ok": 1.5,
            "nested": {"x": float("-inf")},
        }
        provider.log_event(rec)
        records = provider.query_events(run_id, {"event_type": "MARKET_DATA"})
        provider.close()
        assert len(records) == 1

    def test_statement_failure_self_heals(self) -> None:
        """语句级失败（PK 冲突）后连接应 rollback 自愈，后续写入恢复。"""
        run_id = f"heal-{uuid4().hex[:12]}"
        trace_id = f"trace-{uuid4().hex[:12]}"
        a = PostgresAuditProvider()
        b = PostgresAuditProvider()
        try:
            a.log_event(_make_record(run_id=run_id, trace_id=trace_id))
            # b 的 seq 同样从 1 开始 → 同主键 (run_id, trace_id, seq) 冲突
            with pytest.raises(psycopg.errors.UniqueViolation):
                b.log_event(_make_record(run_id=run_id, trace_id=trace_id))
            # 自愈后换 trace_id 再写：应恢复成功
            b.log_event(_make_record(run_id=run_id, trace_id=f"{trace_id}-x"))
        finally:
            a.close()
            b.close()

    def test_connection_drop_self_heals(self) -> None:
        """连接级故障后应丢弃连接，下次写入重连恢复。"""
        run_id = f"drop-{uuid4().hex[:12]}"
        provider = PostgresAuditProvider()
        _log_run_start(provider, run_id)
        provider.log_event(_make_record(run_id=run_id))
        # 故障注入：直接关闭底层连接，模拟服务端断开（不走 close() 的干净路径）
        assert provider._conn is not None
        provider._conn.close()
        with pytest.raises(psycopg.OperationalError):
            provider.log_event(_make_record(run_id=run_id, trace_id="t-drop"))
        # 自愈后下次写入应重连成功
        provider.log_event(_make_record(run_id=run_id, trace_id="t-drop2"))
        provider.close()
