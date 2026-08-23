"""审计批量写入契约测试：缓冲满或 close() 时批量落库，查询前先 flush。

验证目标：
1. log_event 缓冲写入，满 _FLUSH_EVERY（500）条或 close() 时 executemany
   批量落库（长回测数千事件逐条 INSERT+commit 是共享 PG 的热点）；
2. query_events / get_causal_chain 前先 flush 缓冲（read-your-writes：
   保留「写后立即可读」语义，中途查询/监控不丢缓冲数据）；
3. seq 在入缓冲时分配，批量落库保持顺序。

单测全程 Fake 连接（monkeypatch pg_connect），不触碰共享 PostgreSQL。
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from typing import Any

import pytest

from long_earn.backtest.domain.interfaces import AuditRecord
from long_earn.backtest.engine.audit import PostgresAuditProvider


class _FakeCursor:
    """假游标：fetchall 返回空结果集；executemany 记录到宿主连接。"""

    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def executemany(self, sql: str, rows: list[Any]) -> None:
        self._conn.executed.append(f"executemany:{len(rows)}")
        if self._conn.fail_flush_times > 0:
            self._conn.fail_flush_times -= 1
            raise RuntimeError("injected flush failure")

    def fetchall(self) -> list[tuple]:
        return []


class _FakeConn:
    """假连接：记录 execute / executemany 调用；可注入 flush 失败次数。"""

    def __init__(self) -> None:
        self.executed: list[str] = []  # "execute:<sql前缀>" 或 "executemany:<行数>"
        self.closed = False
        self.fail_flush_times: int = 0

    def execute(self, sql: str, params: Any = None) -> _FakeCursor:
        self.executed.append(f"execute:{sql.strip()[:20]}")
        return _FakeCursor(self)

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Any:
    """构造绑定 Fake 连接的 provider（绕过真实 PG 初始化）。"""
    conn = _FakeConn()

    def _fake_pg_connect(**kwargs: Any) -> _FakeConn:
        return conn

    monkeypatch.setattr("long_earn.backtest.engine.audit.pg_connect", _fake_pg_connect)
    prov = PostgresAuditProvider()
    # 初始化 DDL（execute）不算批量写入，清零统计
    conn.executed.clear()
    prov.__dict__["_fake_conn"] = conn
    return prov


def _conn_of(provider: PostgresAuditProvider) -> _FakeConn:
    return provider.__dict__["_fake_conn"]  # type: ignore[no-any-return]


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


def _write_ops(conn: _FakeConn) -> list[str]:
    """仅取批量写入操作（过滤掉 SELECT 查询 execute）。"""
    return [w for w in conn.executed if w.startswith("executemany:")]


def test_buffered_flush_on_close(provider: PostgresAuditProvider) -> None:
    """不足阈值的事件缓冲到 close() 一次性批量落库。"""
    conn = _conn_of(provider)
    for i in range(150):
        provider.log_event(_record(i))

    assert _write_ops(conn) == []  # 未达阈值不落库

    provider.close()
    writes = _write_ops(conn)
    assert writes == ["executemany:150"]


def test_flush_at_threshold(provider: PostgresAuditProvider) -> None:
    """超过 _FLUSH_EVERY 条时中途批量落库一批，close 冲刷余量。"""
    conn = _conn_of(provider)
    for i in range(600):
        provider.log_event(_record(i))

    writes = _write_ops(conn)
    assert writes == ["executemany:500"]  # 满 500 已 flush 一批

    provider.close()
    total = sum(int(w.split(":")[1]) for w in _write_ops(conn))
    assert total == 600


def test_query_events_flushes_buffer(provider: PostgresAuditProvider) -> None:
    """query_events 前先 flush 缓冲（read-your-writes 语义）。"""
    conn = _conn_of(provider)
    for i in range(3):
        provider.log_event(_record(i))
    assert _write_ops(conn) == []

    provider.query_events("bench-run-0", {})

    writes = _write_ops(conn)
    assert writes == ["executemany:3"]


def test_get_causal_chain_flushes_buffer(provider: PostgresAuditProvider) -> None:
    """get_causal_chain 前先 flush 缓冲（read-your-writes 语义）。"""
    conn = _conn_of(provider)
    for i in range(3):
        provider.log_event(_record(i))

    provider.get_causal_chain("trace-0")

    writes = _write_ops(conn)
    assert writes == ["executemany:3"]


def test_seq_assigned_in_buffer_order(provider: PostgresAuditProvider) -> None:
    """seq 在入缓冲时分配：executemany 收到的行序与写入序一致。"""
    captured_rows: list[list[Any]] = []
    orig_executemany = _FakeCursor.executemany

    def _capture(self: _FakeCursor, sql: str, rows: list[Any]) -> None:
        captured_rows.append(list(rows))
        orig_executemany(self, sql, rows)

    _FakeCursor.executemany = _capture  # type: ignore[method-assign]
    try:
        for i in range(3):
            provider.log_event(_record(i))
        provider.close()
    finally:
        _FakeCursor.executemany = orig_executemany  # type: ignore[method-assign]

    assert len(captured_rows) == 1
    seqs = [row[1] for row in captured_rows[0]]
    assert seqs == sorted(seqs) == [1, 2, 3]


# ── 失败路径契约（C1/C2）：flush 失败不丢整批、close 不泄漏连接 ──────


def test_flush_failure_refills_buffer(
    provider: PostgresAuditProvider,
) -> None:
    """flush 失败（瞬态）时整批回填缓冲，自愈后重试成功——事件不丢。"""
    conn = _conn_of(provider)
    for i in range(3):
        provider.log_event(_record(i))

    # 注入瞬态失败：query 触发 flush 抛错
    conn.fail_flush_times = 1
    with pytest.raises(RuntimeError):
        provider.query_events("bench-run-0", {})

    # 自愈后（无故障）再次 flush：回填的 3 条全部落库
    provider.query_events("bench-run-0", {})
    writes = _write_ops(conn)
    assert writes == ["executemany:3", "executemany:3"]
    assert provider._buffer == []  # 缓冲已清空


def test_close_flush_failure_still_closes_conn(
    provider: PostgresAuditProvider,
) -> None:
    """close 冲刷失败（含重试）时连接仍被关闭——不泄漏句柄。"""
    conn = _conn_of(provider)
    for i in range(3):
        provider.log_event(_record(i))

    # 持续失败：close 的首次冲刷与重试都失败
    conn.fail_flush_times = 10
    provider.close()

    assert conn.closed is True  # C1：连接必关
    assert provider._conn is None
    assert provider._buffer == []  # 缓冲被丢弃（有界），不再无限滞留


def test_close_flush_transient_failure_retry_recovers(
    provider: PostgresAuditProvider,
) -> None:
    """close 冲刷第一次失败但重试成功时，缓冲事件不丢。"""
    conn = _conn_of(provider)
    for i in range(3):
        provider.log_event(_record(i))

    conn.fail_flush_times = 1  # 仅首次失败，重试成功
    provider.close()

    assert conn.closed is True
    writes = _write_ops(conn)
    assert writes == ["executemany:3", "executemany:3"]  # 失败一次后重试成功


def test_buffer_bounded_on_permanent_failure(
    provider: PostgresAuditProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """永久失败下缓冲有上限：溢出丢弃最旧事件，不无限增长。"""
    from long_earn.backtest.engine.audit import PostgresAuditProvider as P

    monkeypatch.setattr(P, "_FLUSH_EVERY", 5)
    monkeypatch.setattr(P, "_MAX_BUFFER", 10)
    conn = _conn_of(provider)

    # 持续故障：每次 flush 都失败（永久性，如 PK 冲突）
    orig_executemany = _FakeCursor.executemany

    def _always_fail(self: _FakeCursor, sql: str, rows: list[Any]) -> None:
        conn.executed.append(f"executemany:{len(rows)}")
        raise RuntimeError("permanent pk conflict")

    _FakeCursor.executemany = _always_fail  # type: ignore[method-assign]
    try:
        for i in range(30):
            # 模拟引擎 _log_audit：审计写入失败不阻断主流程（吞异常）
            with contextlib.suppress(RuntimeError):
                provider.log_event(_record(i))
    finally:
        _FakeCursor.executemany = orig_executemany  # type: ignore[method-assign]

    assert len(provider._buffer) <= 10  # 有界


def test_connect_failure_refills_buffer(
    provider: PostgresAuditProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """连接失败（_get_conn 抛错）时整批回填缓冲——一批事件不静默丢失。

    回归守护：``_get_conn`` 曾在 try 块外，连接失败（PG 不可达）时 rows
    已从 buffer 取出但异常跳过回填——最多一批事件静默丢失且无日志，
    而引擎 ``_log_audit`` 吞审计异常继续跑，审计链无声断裂。
    """
    conn = _conn_of(provider)
    for i in range(3):
        provider.log_event(_record(i))

    # 模拟 PG 不可达：丢弃现有连接 + 重连抛错
    provider._conn = None

    def _connect_fail(**kwargs: Any) -> _FakeConn:
        raise RuntimeError("pg unreachable")

    monkeypatch.setattr("long_earn.backtest.engine.audit.pg_connect", _connect_fail)
    with pytest.raises(RuntimeError, match="pg unreachable"):
        provider.query_events("bench-run-0", {})

    # 整批仍在缓冲中（未静默丢失）
    assert len(provider._buffer) == 3

    # PG 恢复：重连成功，回填的事件完整落库
    monkeypatch.setattr("long_earn.backtest.engine.audit.pg_connect", lambda **kw: conn)
    provider.query_events("bench-run-0", {})
    writes = _write_ops(conn)
    assert writes == ["executemany:3"]
    assert provider._buffer == []
