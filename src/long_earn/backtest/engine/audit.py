import json
import math
import threading
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any

from loguru import logger

from long_earn.backtest.domain.interfaces import AuditProvider, AuditRecord
from long_earn.core.db import exec_sql, read_connection, write_transaction

# query_events 过滤字段白名单 — 防止 key 拼接 SQL 注入（P2-13）
_QUERY_FILTER_WHITELIST = frozenset(
    {"event_type", "trace_id", "parent_id", "component", "status", "latency_ms"}
)

# 审计表 DDL（PostgreSQL 方言，schema 名与 DuckDB 时代一致）
_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS backtest_audit.logs (
    run_id VARCHAR,
    seq BIGINT,
    timestamp TIMESTAMP,
    event_type VARCHAR,
    trace_id VARCHAR,
    parent_id VARCHAR,
    component VARCHAR,
    status VARCHAR,
    payload JSONB,
    latency_ms DOUBLE PRECISION,
    PRIMARY KEY (run_id, trace_id, seq)
)
"""
_AUDIT_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_logs_run ON backtest_audit.logs(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_logs_trace ON backtest_audit.logs(trace_id)",
)

# 专用测试标签：测试/冒烟回测写入共享 PG 时必须在 RUN_START payload.tags
# 携带本标签，供审计库「清理带 test 标签记录」口径识别与批量清理。
RUN_TAG_TEST = "test"
# 生产保护标签：策略 DSL 声明 ``kind: production`` 时引擎自动携带，
# 供审计库「清理带 test 标签记录」口径豁免（test 且不含 prod 才清）。
RUN_TAG_PROD = "prod"


class OrderSkipReason(StrEnum):
    """订单跳过原因枚举（AUDIT-P2-03）。

    替代自由文本字符串，使审计日志中 ORDER_SKIPPED 事件的 reason 字段
    结构化、可查询、可聚合。
    """

    T1_LOCKED = "T1_LOCKED"
    """T+1 锁定：卖出日早于持仓可用日"""

    LIMIT_UP_REJECT = "LIMIT_UP_REJECT"
    """涨停拒买：买入价达到涨停价"""

    LIMIT_DOWN_REJECT = "LIMIT_DOWN_REJECT"
    """跌停拒卖：卖出价达到跌停价"""

    SUSPENDED = "SUSPENDED"
    """停牌拒单：is_tradable=False 或成交量==0"""

    PRICE_NOT_FOUND = "PRICE_NOT_FOUND"
    """价格缺失：slab 中找不到该标的的价格"""

    PRICE_INVALID = "PRICE_INVALID"
    """价格无效：NaN / Inf / 非正数（成交价/市场价）"""

    INVALID_PRICE = "INVALID_PRICE"
    """订单价格无效：限价/止损价的 NaN / Inf / 非正数（P3-02）"""

    INVALID_QUANTITY = "INVALID_QUANTITY"
    """订单数量无效：NaN / Inf / 非正数（0 及负数，P3-02）"""

    CASH_INSUFFICIENT = "CASH_INSUFFICIENT"
    """买入现金不足：成交时点组合校验失败，跳过该笔交易（P2-02）"""

    POSITION_INSUFFICIENT = "POSITION_INSUFFICIENT"
    """卖出超持仓/无持仓：仅多头约束下拒绝凭空增资的卖出成交"""


def _sanitize_json_value(obj: Any) -> Any:
    """递归把 NaN/±Inf 转为 None。

    ``json.dumps`` 默认把 NaN/Infinity 序列化为 ``NaN``/``Infinity`` 字面量
    （非合法 JSON），PG jsonb 列会拒绝并抛 ``invalid input syntax for type
    json`` —— 这是审计连接被毒化的已知第一因之一。
    """
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_json_value(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_json_value(v) for v in obj]
    return obj


class PostgresAuditProvider(AuditProvider):
    """PostgreSQL 实现的审计存储提供者

    审计日志写入 PostgreSQL（``backtest_audit.logs``），替代 DuckDB 时代
    的本地文件存储。多进程并行回测的 worker 直接并发写同一 PG 表——
    PostgreSQL MVCC 原生支持多写者并发，消除「worker 临时文件 + 主进程
    合并」的旧架构（P0-11 缺口根治）。

    批量写入：``log_event`` 缓冲事件，满 ``_FLUSH_EVERY`` 条或 ``close()``
    时以 ``exec_sql`` executemany 批量落库。``query_events`` /
    ``get_causal_chain`` 查询前先 flush 缓冲，保留「写后立即可读」语义。

    连接：不持有实例级长连接。缓冲在内存；flush/查询时分别进入
    ``write_transaction`` / ``read_connection``，块退出归还连接池
    （语句失败由引擎层 rollback 自愈，替代手工 ``_heal_after_error``）。

    线程安全：``_lock`` 保护 seq 与缓冲；并发 flush 串行化以免缓冲交错。

    单调序列号：``seq`` 列确保即使墙钟回退也能保证主键唯一性与因果排序
    （P1-10）；seq 在入缓冲时分配，批量落库保持缓冲顺序。
    """

    _FLUSH_EVERY = 500

    _MAX_BUFFER = 10_000
    """缓冲上限：永久性 flush 失败（如 PK 冲突）下丢弃最旧事件并记
    error 日志，保证内存有界；瞬态失败经回填重试不触此限。"""

    _INSERT_SQL = """
                    INSERT INTO "backtest_audit".logs
                        (run_id, seq, timestamp, event_type, trace_id, parent_id, component, status, payload, latency_ms)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        self._buffer: list[tuple[Any, ...]] = []
        self._init_db()

    def _init_db(self) -> None:
        with write_transaction() as conn:
            exec_sql(conn, 'CREATE SCHEMA IF NOT EXISTS "backtest_audit"')
            exec_sql(conn, _AUDIT_DDL)
            for idx in _AUDIT_INDEXES:
                exec_sql(conn, idx)
        logger.info("Audit provider initialized (PostgreSQL)")

    def close(self) -> None:
        """冲刷缓冲后归还（无实例连接可关）。

        冲刷失败时自愈后重试一次；重试仍失败则丢弃缓冲并记 error。
        close 不抛异常。
        """
        with self._lock:
            try:
                self._flush_locked()
            except Exception as exc:
                logger.error(f"[audit] close 冲刷失败，自愈后重试一次: {exc}")
                try:
                    self._flush_locked()
                except Exception as exc2:
                    n = len(self._buffer)
                    logger.error(
                        f"[audit] close 重试仍失败，丢弃缓冲 {n} 条审计事件"
                        f"（审计链可能缺失尾部事件）: {exc2}"
                    )
                    self._buffer = []

    def log_event(self, record: AuditRecord) -> None:
        """缓冲写入；满 ``_FLUSH_EVERY`` 条批量落库（executemany 一次提交）。"""

        def json_serializable(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            if hasattr(obj, "__dict__"):
                return obj.__dict__
            return str(obj)

        payload_json = json.dumps(
            _sanitize_json_value(record.payload), default=json_serializable
        )

        with self._lock:
            self._seq += 1
            seq = self._seq
            self._buffer.append(
                (
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
                )
            )
            if len(self._buffer) >= self._FLUSH_EVERY:
                self._flush_locked()

    def _flush_locked(self) -> None:
        """批量落库缓冲事件（调用方须已持 ``self._lock``）。

        失败语义：语句失败后**整批回填缓冲**并上抛——瞬态故障在下一次
        flush（后续 log_event / 查询 / close）自动重试，事件不丢。
        永久性失败由 ``_MAX_BUFFER`` 上限截断。
        连接在 ``write_transaction`` 块内借还，失败由引擎层 rollback。
        """
        if not self._buffer:
            return
        rows, self._buffer = self._buffer, []
        try:
            with write_transaction() as conn:
                exec_sql(conn, self._INSERT_SQL, rows)
        except Exception:
            self._buffer = rows + self._buffer
            self._truncate_buffer_locked()
            raise

    def _truncate_buffer_locked(self) -> None:
        """缓冲超 ``_MAX_BUFFER`` 时丢弃最旧事件（调用方须已持锁）。"""
        overflow = len(self._buffer) - self._MAX_BUFFER
        if overflow > 0:
            del self._buffer[:overflow]
            logger.error(
                f"[audit] 缓冲超上限 {self._MAX_BUFFER}，丢弃最旧 {overflow} 条"
                "审计事件（flush 持续失败，审计链存在空洞）"
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
            "SELECT run_id, timestamp, event_type, trace_id, parent_id, "
            "component, status, payload, latency_ms "
            'FROM "backtest_audit".logs WHERE run_id = %s'
        )
        params: list[Any] = [run_id]

        for key, value in filters.items():
            query += f" AND {key} = %s"
            params.append(value)

        with self._lock:
            # read-your-writes：查询前冲刷缓冲，保证本进程已写事件可见
            self._flush_locked()
            with read_connection() as conn:
                res = exec_sql(conn, query, params).fetchall()

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
                    payload=json.loads(row[7]) if isinstance(row[7], str) else row[7],
                    latency_ms=row[8],
                )
            )
        return records

    def get_causal_chain(self, trace_id: str) -> Sequence[AuditRecord]:
        # 按 seq 排序保证因果链单调性（P1-10：timestamp 可能因墙钟回退无序）
        with self._lock:
            # read-your-writes：查询前冲刷缓冲
            self._flush_locked()
            with read_connection() as conn:
                res = exec_sql(
                    conn,
                    "SELECT run_id, timestamp, event_type, trace_id, parent_id, "
                    "component, status, payload, latency_ms "
                    'FROM "backtest_audit".logs WHERE trace_id = %s ORDER BY seq ASC',
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
                    payload=json.loads(row[7]) if isinstance(row[7], str) else row[7],
                    latency_ms=row[8],
                )
            )
        return records


class AuditLogger:
    """
    审计记录器的高层封装

    将回测引擎的领域事件转换为 AuditRecord 并通过 Provider 持久化。
    """

    def __init__(self, provider: AuditProvider, run_id: str):
        self.provider = provider
        self.run_id = run_id

    def log_run_start(self, payload: dict[str, Any]) -> None:
        """记录 RUN_START 事件（run 锚点）。

        RUN_START 是 run 级元数据的载体（strategy_id / tags / 回测配置等），
        测试/冒烟回测必须在此携带 ``RUN_TAG_TEST`` 标签，供审计库按
        「带 test 标签」口径清理。
        """
        self.log_transition(
            event_type="RUN_START",
            trace_id=self.run_id,
            component="Engine",
            status="SUCCESS",
            payload=payload,
        )

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
