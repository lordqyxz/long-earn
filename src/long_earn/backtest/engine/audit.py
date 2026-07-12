import json
import threading
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
from loguru import logger

from long_earn.backtest.domain.interfaces import AuditProvider, AuditRecord
from long_earn.core.storage import backtest_cache_path

# query_events 过滤字段白名单 — 防止 key 拼接 SQL 注入（P2-13）
_QUERY_FILTER_WHITELIST = frozenset(
    {"event_type", "trace_id", "parent_id", "component", "status", "latency_ms"}
)


class DuckDBAuditProvider(AuditProvider):
    """DuckDB 实现的审计存储提供者

    线程安全：所有 DuckDB 连接访问通过 ``_lock`` 串行化，避免多线程并发写
    导致 DuckDB 单连接非线程安全问题（P2-14）。

    单调序列号：``seq`` 列确保即使墙钟回退（``datetime.now()`` 非单调）也能
    保证主键唯一性与因果排序（P1-10）。
    """

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path if db_path is not None else backtest_cache_path()
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._lock = threading.Lock()
        self._seq = 0
        self._init_db()

    def _get_conn(self) -> duckdb.DuckDBPyConnection:
        # 锁保护：调用方已持锁，或通过 _with_conn 上下文访问
        if self._conn is None:
            self._conn = duckdb.connect(str(self.db_path))
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            # 创建审计专用 Schema（使用唯一名称避免与数据库文件名冲突）
            conn.execute('CREATE SCHEMA IF NOT EXISTS "backtest_audit"')
            # 创建审计日志表 — seq 自增序列号保证单调性（P1-10）
            conn.execute("""
                CREATE TABLE IF NOT EXISTS "backtest_audit".logs (
                    run_id VARCHAR,
                    seq BIGINT,
                    timestamp TIMESTAMP,
                    event_type VARCHAR,
                    trace_id VARCHAR,
                    parent_id VARCHAR,
                    component VARCHAR,
                    status VARCHAR,
                    payload JSON,
                    latency_ms DOUBLE,
                    PRIMARY KEY (run_id, trace_id, seq)
                )
            """)
            # 旧表迁移：若 seq 列不存在则添加（向后兼容已有缓存）
            cols = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='backtest_audit' AND table_name='logs'"
            ).fetchall()
            col_names = {c[0] for c in cols}
            if "seq" not in col_names and len(col_names) > 0:
                conn.execute(
                    'ALTER TABLE "backtest_audit".logs ADD COLUMN seq BIGINT DEFAULT 0'
                )
        logger.info(f"Audit provider initialized at {self.db_path}")

    def log_event(self, record: AuditRecord) -> None:
        def json_serializable(obj):

            if isinstance(obj, datetime):
                return obj.isoformat()
            if hasattr(obj, "__dict__"):
                return obj.__dict__
            return str(obj)

        payload_json = json.dumps(record.payload, default=json_serializable)

        with self._lock:
            self._seq += 1
            seq = self._seq
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO "backtest_audit".logs
                    (run_id, seq, timestamp, event_type, trace_id, parent_id, component, status, payload, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    record.run_id,
                    seq,
                    record.timestamp,
                    record.event_type,
                    record.trace_id,
                    record.parent_id,
                    record.component,
                    record.status,
                    payload_json,
                    record.latency_ms,
                ],
            )

    def query_events(
        self, run_id: str, filters: dict[str, Any]
    ) -> Sequence[AuditRecord]:
        # P2-13：对 filters key 做白名单校验，防止 SQL 注入
        invalid_keys = set(filters.keys()) - _QUERY_FILTER_WHITELIST
        if invalid_keys:
            raise ValueError(
                f"query_events 不允许的过滤字段（非白名单）: {sorted(invalid_keys)}。"
                f"允许字段: {sorted(_QUERY_FILTER_WHITELIST)}"
            )

        query = (
            'SELECT run_id, timestamp, event_type, trace_id, parent_id, '
            'component, status, payload, latency_ms '
            'FROM "backtest_audit".logs WHERE run_id = ?'
        )
        params: list[Any] = [run_id]

        for key, value in filters.items():
            query += f" AND {key} = ?"
            params.append(value)

        with self._lock:
            conn = self._get_conn()
            res = conn.execute(query, params).fetchall()

        records = []
        for row in res:
            records.append(
                AuditRecord(
                    run_id=row[0],
                    timestamp=row[1],
                    event_type=row[2],
                    trace_id=row[3],
                    parent_id=row[4],
                    component=row[5],
                    status=row[6],
                    payload=json.loads(row[7]),
                    latency_ms=row[8],
                )
            )
        return records

    def get_causal_chain(self, trace_id: str) -> Sequence[AuditRecord]:
        # 按 seq 排序保证因果链单调性（P1-10：timestamp 可能因墙钟回退无序）
        with self._lock:
            conn = self._get_conn()
            res = conn.execute(
                'SELECT run_id, timestamp, event_type, trace_id, parent_id, '
                'component, status, payload, latency_ms '
                'FROM "backtest_audit".logs WHERE trace_id = ? ORDER BY seq ASC',
                [trace_id],
            ).fetchall()

        records = []
        for row in res:
            records.append(
                AuditRecord(
                    run_id=row[0],
                    timestamp=row[1],
                    event_type=row[2],
                    trace_id=row[3],
                    parent_id=row[4],
                    component=row[5],
                    status=row[6],
                    payload=json.loads(row[7]),
                    latency_ms=row[8],
                )
            )
        return records

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None


class AuditLogger:
    """
    审计记录器的高层封装

    将回测引擎的领域事件转换为 AuditRecord 并通过 Provider 持久化。
    """

    def __init__(self, provider: AuditProvider, run_id: str):
        self.provider = provider
        self.run_id = run_id

    def log_transition(  # noqa: PLR0913
        self,
        event_type: str,
        trace_id: str,
        component: str,
        status: str,
        payload: dict[str, Any],
        parent_id: str | None = None,
        timestamp: Any = None,
        latency_ms: float | None = None,
    ) -> None:
        """记录一次状态转换/事件执行"""

        record = AuditRecord(
            run_id=self.run_id,
            timestamp=timestamp or datetime.now(),
            event_type=event_type,
            trace_id=trace_id,
            parent_id=parent_id,
            component=component,
            status=status,
            payload=payload,
            latency_ms=latency_ms,
        )
        self.provider.log_event(record)
