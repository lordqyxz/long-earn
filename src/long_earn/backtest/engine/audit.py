import json
import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from long_earn.backtest.data.cache import DEFAULT_CACHE_PATH
from long_earn.backtest.domain.interfaces import AuditProvider, AuditRecord

logger = logging.getLogger(__name__)


class DuckDBAuditProvider(AuditProvider):
    """DuckDB 实现的审计存储提供者"""

    def __init__(self, db_path: Path = DEFAULT_CACHE_PATH):
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._init_db()

    def _get_conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self._conn = duckdb.connect(str(self.db_path))
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        # 创建审计专用 Schema（使用唯一名称避免与数据库文件名冲突）
        conn.execute('CREATE SCHEMA IF NOT EXISTS "backtest_audit"')
        # 创建审计日志表
        conn.execute('''
            CREATE TABLE IF NOT EXISTS "backtest_audit".logs (
                run_id VARCHAR,
                timestamp TIMESTAMP,
                event_type VARCHAR,
                trace_id VARCHAR,
                parent_id VARCHAR,
                component VARCHAR,
                status VARCHAR,
                payload JSON,
                latency_ms DOUBLE,
                PRIMARY KEY (run_id, trace_id, timestamp)
            )
        ''')
        logger.info(f"Audit provider initialized at {self.db_path}")

    def log_event(self, record: AuditRecord) -> None:
        conn = self._get_conn()

        def json_serializable(obj):

            if isinstance(obj, datetime):
                return obj.isoformat()
            if hasattr(obj, "__dict__"):
                return obj.__dict__
            return str(obj)

        payload_json = json.dumps(record.payload, default=json_serializable)

        conn.execute(
            '''
            INSERT INTO "backtest_audit".logs (run_id, timestamp, event_type, trace_id, parent_id, component, status, payload, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            [
                record.run_id,
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
        conn = self._get_conn()
        query = 'SELECT * FROM "backtest_audit".logs WHERE run_id = ?'
        params = [run_id]

        for key, value in filters.items():
            query += f" AND {key} = ?"
            params.append(value)

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
        conn = self._get_conn()
        res = conn.execute(
            'SELECT * FROM "backtest_audit".logs WHERE trace_id = ? ORDER BY timestamp ASC',
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
