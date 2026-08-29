import json
import math
import threading
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any

import psycopg
from loguru import logger

from long_earn.backtest.domain.interfaces import AuditProvider, AuditRecord
from long_earn.core.pg import pg_connect

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
    时以 ``executemany`` 批量落库（长回测数千事件，逐条 INSERT+commit
    在共享 PG 上是网格并发的热点）。``query_events`` / ``get_causal_chain``
    查询前先 flush 缓冲，保留「写后立即可读」语义。

    线程安全：进程内所有 DuckDB 时代的锁语义不再需要——psycopg 连接
    每次操作独立提交，PG 服务端处理并发；本实现保留单连接 + 锁以兼容
    单进程多线程写入（引擎审计为旁路，性能非瓶颈）。

    单调序列号：``seq`` 列确保即使墙钟回退（``datetime.now()`` 非单调）也能
    保证主键唯一性与因果排序（P1-10）；seq 在入缓冲时分配，批量落库
    保持缓冲顺序。
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
        # 连接参数由 core.pg 统一裁决
        self._conn: psycopg.Connection | None = None
        self._lock = threading.Lock()
        self._seq = 0
        self._buffer: list[tuple[Any, ...]] = []
        self._init_db()

    def _get_conn(self) -> psycopg.Connection:
        # 锁保护：调用方已持锁，或通过 _with_conn 上下文访问
        if self._conn is None:
            # 元组行：query_events / get_causal_chain 用 row[N] 下标访问，
            # 保持 DuckDB 时代 fetchall 返回元组的契约（PG jsonb 由
            # psycopg 反序列化为 dict，兼容性由调用方处理）
            self._conn = pg_connect(row_factory=None)
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            # 创建审计专用 Schema（与 DuckDB 时代同名，迁移无缝）
            conn.execute('CREATE SCHEMA IF NOT EXISTS "backtest_audit"')
            conn.execute(_AUDIT_DDL)
            for idx in _AUDIT_INDEXES:
                conn.execute(idx)
            conn.commit()
        logger.info("Audit provider initialized (PostgreSQL)")

    def close(self) -> None:
        """显式关闭连接（关闭前冲刷缓冲，保证审计完整性）。

        冲刷失败（如 PK 冲突 / PG 不可达）时自愈后重试一次；重试仍失败
        则丢弃缓冲并记 error 日志（含条数）——close 不抛异常，但连接必关
        （避免 flush 异常跳过 ``conn.close()`` 导致句柄泄漏 + RUN_END
        静默丢失）。
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
            finally:
                if self._conn is not None:
                    try:
                        self._conn.close()
                    except Exception as exc:
                        logger.warning(f"关闭审计 PostgreSQL 连接时异常: {exc}")
                    finally:
                        self._conn = None

    def _heal_after_error(self, exc: Exception) -> None:
        """语句/连接失败后的自愈，防止连接被毒化。

        psycopg3 的隐式事务在语句失败后进入 aborted 状态，之后所有语句
        都抛 ``InFailedSqlTransaction`` 且永不自愈（调用方持有的异常只是
        第一因，后续全部是连锁失败）；必须 ``rollback()`` 清除。连接级
        故障（服务端断开等）则丢弃连接，下次操作重连。
        """
        conn = self._conn
        if conn is None:
            return
        try:
            if getattr(conn, "closed", False) or isinstance(
                exc, psycopg.OperationalError
            ):
                conn.close()
                self._conn = None
            else:
                conn.rollback()
        except Exception:
            self._conn = None

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

        失败语义：语句失败自愈连接后**整批回填缓冲**并上抛——瞬态故障
        （PG 重启等）在下一次 flush（后续 log_event / 查询 / close）自动
        重试，事件不丢；批量写入相对逐条写入把单次失败的最大丢失量从 1 条
        放大到一批，回填即为此设计。永久性失败（如 PK 冲突）经回填反复
        重试仍失败时，由 ``_MAX_BUFFER`` 上限截断（丢弃最旧事件并记
        error 日志），保证内存有界。
        psycopg3 的批量写入走 cursor.executemany（Connection 无此便捷方法）。
        """
        if not self._buffer:
            return
        rows, self._buffer = self._buffer, []
        try:
            # _get_conn 也须在 try 内：连接失败（PG 不可达）时整批回填缓冲，
            # 否则 rows 已取出而异常跳过回填——最多一批事件静默丢失且无日志
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.executemany(self._INSERT_SQL, rows)
            conn.commit()
        except Exception as exc:
            self._heal_after_error(exc)
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
            conn = self._get_conn()
            try:
                # key 已过白名单（_QUERY_FILTER_WHITELIST），无注入风险；
                # psycopg 严格参数类型把 str 标为 QueryNoTemplate，运行时兼容
                res = conn.execute(query, params).fetchall()  # type: ignore[arg-type]
            except Exception as exc:
                self._heal_after_error(exc)
                raise

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
            conn = self._get_conn()
            try:
                res = conn.execute(
                    "SELECT run_id, timestamp, event_type, trace_id, parent_id, "
                    "component, status, payload, latency_ms "
                    'FROM "backtest_audit".logs WHERE trace_id = %s ORDER BY seq ASC',
                    [trace_id],
                ).fetchall()
            except Exception as exc:
                self._heal_after_error(exc)
                raise

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
