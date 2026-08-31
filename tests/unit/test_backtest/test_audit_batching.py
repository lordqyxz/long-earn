"""审计批量写入契约测试：缓冲满或 close() 时批量落库，查询前先 flush。

验证目标：
1. log_event 缓冲写入，满 _FLUSH_EVERY（500）条或 close() 时 executemany
   批量落库；
2. query_events / get_causal_chain 前先 flush 缓冲（read-your-writes）；
3. seq 在入缓冲时分配，批量落库保持顺序；
4. flush 失败整批回填；连接失败不静默丢事件；无实例长连接可泄漏。

单测全程 Fake 连接（monkeypatch write_transaction / read_connection），
不触碰共享 PostgreSQL。
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import pytest

from long_earn.backtest.domain.interfaces import AuditRecord
from long_earn.backtest.engine.audit import PostgresAuditProvider


class _FakeResult:
    def fetchall(self) -> list[tuple]:
        return []

    def fetchone(self) -> None:
        return None


class _FakeSaConn:
    """假 SQLAlchemy Connection：exec_sql → exec_driver_sql。"""

    def __init__(self, host: _FakeHost) -> None:
        self._host = host

    def exec_driver_sql(self, sql: str, params: Any = None) -> _FakeResult:
        host = self._host
        if (
            params is not None
            and isinstance(params, list)
            and len(params) > 0
            and isinstance(params[0], tuple)
        ):
            host.executed.append(f"executemany:{len(params)}")
            host.captured_rows.append(list(params))
            if host.fail_flush_times > 0:
                host.fail_flush_times -= 1
                raise RuntimeError("injected flush failure")
            return _FakeResult()
        host.executed.append(f"execute:{sql.strip()[:20]}")
        return _FakeResult()


class _FakeHost:
    """记录 executemany 调用，可注入 flush / 连接失败。"""

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.captured_rows: list[list[Any]] = []
        self.fail_flush_times: int = 0
        self.connect_fail: bool = False


@contextmanager
def _fake_tx(host: _FakeHost) -> Iterator[_FakeSaConn]:
    if host.connect_fail:
        raise RuntimeError("pg unreachable")
    yield _FakeSaConn(host)


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Any:
    """构造绑定 Fake 事务上下文的 provider（绕过真实 PG）。"""
    host = _FakeHost()

    def _tx() -> Iterator[_FakeSaConn]:
        return _fake_tx(host)

    monkeypatch.setattr(
        "long_earn.backtest.engine.audit.write_transaction", _tx
    )
    monkeypatch.setattr(
        "long_earn.backtest.engine.audit.read_connection", _tx
    )
    prov = PostgresAuditProvider()
    host.executed.clear()
    host.captured_rows.clear()
    prov.__dict__["_fake_host"] = host
    return prov


def _host_of(provider: PostgresAuditProvider) -> _FakeHost:
    return provider.__dict__["_fake_host"]  # type: ignore[no-any-return]


def _record(seq: int) -> AuditRecord:
    return AuditRecord(
        run_id=f"bench-run-{seq}",
        timestamp=datetime(2024, 1, 1),
        event_type="MARKET_DATA",
        trace_id=f"trace-{seq}",
        parent_id=None,
        component="engine",
        status="OK",
        payload={"seq": seq},
        latency_ms=0.5,
    )


def _write_ops(host: _FakeHost) -> list[str]:
    return [w for w in host.executed if w.startswith("executemany:")]


def test_buffered_flush_on_close(provider: PostgresAuditProvider) -> None:
    """不足阈值的事件缓冲到 close() 一次性批量落库。"""
    host = _host_of(provider)
    for i in range(150):
        provider.log_event(_record(i))

    assert _write_ops(host) == []

    provider.close()
    assert _write_ops(host) == ["executemany:150"]


def test_flush_at_threshold(provider: PostgresAuditProvider) -> None:
    """超过 _FLUSH_EVERY 条时中途批量落库一批，close 冲刷余量。"""
    host = _host_of(provider)
    for i in range(600):
        provider.log_event(_record(i))

    assert _write_ops(host) == ["executemany:500"]

    provider.close()
    total = sum(int(w.split(":")[1]) for w in _write_ops(host))
    assert total == 600


def test_query_events_flushes_buffer(provider: PostgresAuditProvider) -> None:
    """query_events 前先 flush 缓冲（read-your-writes 语义）。"""
    host = _host_of(provider)
    for i in range(3):
        provider.log_event(_record(i))
    assert _write_ops(host) == []

    provider.query_events("bench-run-0", {})

    assert _write_ops(host) == ["executemany:3"]


def test_get_causal_chain_flushes_buffer(provider: PostgresAuditProvider) -> None:
    """get_causal_chain 前先 flush 缓冲（read-your-writes 语义）。"""
    host = _host_of(provider)
    for i in range(3):
        provider.log_event(_record(i))

    provider.get_causal_chain("trace-0")

    assert _write_ops(host) == ["executemany:3"]


def test_seq_assigned_in_buffer_order(provider: PostgresAuditProvider) -> None:
    """seq 在入缓冲时分配：executemany 收到的行序与写入序一致。"""
    for i in range(3):
        provider.log_event(_record(i))
    provider.close()

    host = _host_of(provider)
    assert len(host.captured_rows) == 1
    seqs = [row[1] for row in host.captured_rows[0]]
    assert seqs == sorted(seqs) == [1, 2, 3]


def test_flush_failure_refills_buffer(
    provider: PostgresAuditProvider,
) -> None:
    """flush 失败（瞬态）时整批回填缓冲，自愈后重试成功——事件不丢。"""
    host = _host_of(provider)
    for i in range(3):
        provider.log_event(_record(i))

    host.fail_flush_times = 1
    with pytest.raises(RuntimeError):
        provider.query_events("bench-run-0", {})

    provider.query_events("bench-run-0", {})
    assert _write_ops(host) == ["executemany:3", "executemany:3"]
    assert provider._buffer == []


def test_close_flush_failure_discards_buffer(
    provider: PostgresAuditProvider,
) -> None:
    """close 冲刷失败（含重试）时丢弃缓冲；无实例连接可泄漏。"""
    host = _host_of(provider)
    for i in range(3):
        provider.log_event(_record(i))

    host.fail_flush_times = 10
    provider.close()

    assert not hasattr(provider, "_conn")
    assert provider._buffer == []


def test_close_flush_transient_failure_retry_recovers(
    provider: PostgresAuditProvider,
) -> None:
    """close 冲刷第一次失败但重试成功时，缓冲事件不丢。"""
    host = _host_of(provider)
    for i in range(3):
        provider.log_event(_record(i))

    host.fail_flush_times = 1
    provider.close()

    assert _write_ops(host) == ["executemany:3", "executemany:3"]
    assert provider._buffer == []


def test_buffer_bounded_on_permanent_failure(
    provider: PostgresAuditProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """永久失败下缓冲有上限：溢出丢弃最旧事件，不无限增长。"""
    from long_earn.backtest.engine.audit import PostgresAuditProvider as P

    monkeypatch.setattr(P, "_FLUSH_EVERY", 5)
    monkeypatch.setattr(P, "_MAX_BUFFER", 10)
    host = _host_of(provider)

    def _always_fail(
        self: _FakeSaConn, sql: str, params: Any = None
    ) -> _FakeResult:
        if (
            params is not None
            and isinstance(params, list)
            and len(params) > 0
            and isinstance(params[0], tuple)
        ):
            host.executed.append(f"executemany:{len(params)}")
            raise RuntimeError("permanent pk conflict")
        return _FakeResult()

    monkeypatch.setattr(_FakeSaConn, "exec_driver_sql", _always_fail)
    try:
        for i in range(30):
            with contextlib.suppress(RuntimeError):
                provider.log_event(_record(i))
    finally:
        pass

    assert len(provider._buffer) <= 10


def test_connect_failure_refills_buffer(
    provider: PostgresAuditProvider,
) -> None:
    """连接失败时整批回填缓冲——一批事件不静默丢失。"""
    host = _host_of(provider)
    for i in range(3):
        provider.log_event(_record(i))

    host.connect_fail = True
    with pytest.raises(RuntimeError, match="pg unreachable"):
        provider.query_events("bench-run-0", {})

    assert len(provider._buffer) == 3

    host.connect_fail = False
    provider.query_events("bench-run-0", {})
    assert _write_ops(host) == ["executemany:3"]
    assert provider._buffer == []
