"""SQLAlchemy 2.0 Core 引擎层 — 数据底座的统一连接/事务入口。

连接参数仍由 ``core.pg`` 单一裁决；本模块只负责引擎生命周期与
连接/事务语义。仅 Core，不用 ORM（COPY 百万行装载、polars/pandas
直读的负载与 ORM 会话模型冲突）。

设计要点：
- **进程级单例 Engine**：池化短连接。Engine 不可跨进程 pickle——
  spawn worker 子进程首次调用时惰性自建。
- **禁止长占池连接**：不得把池连接挂在实例字段上跨请求/跨事件持有。
  审计缓冲等应用状态放在内存，flush 时再 ``write_transaction()``。
- **读路径**：``read_connection()`` 短上下文，归还自动 rollback；
  ``read_only=True`` 时设置底层 psycopg ``read_only``，分析侧防误写；
  **退出必须复位**（会话级参数，池 rollback 不清，否则污染后续写事务）。
- **写路径**：``write_transaction()``（``engine.begin()``）：成功
  commit / 异常 rollback，连接归还时复位（替代手工自愈）。
- **连接超时**：``connect_timeout=5``，缺库环境快速失败而非挂死。
- **COPY / executemany**：只允许在上述上下文块内。COPY 经
  ``raw_psycopg_connection()`` 逃生舱；批量 DML 经 ``exec_sql``
  传入「元组/字典元素的列表」走驱动 executemany。
- **SQL 风格**：``exec_sql`` 保持 psycopg ``%s`` 占位符；行消费走
  Row 整数下标（DuckDB 时代 fetchall 元组契约）。
- **DDL**：构造即建表，不引入 alembic。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from sqlalchemy import URL, Connection as _SaConnection, Engine, create_engine
from sqlalchemy.engine import CursorResult

from long_earn.core.pg import resolve_pg_params

__all__ = [
    "db_version",
    "exec_sql",
    "get_engine",
    "raw_psycopg_connection",
    "read_connection",
    "write_transaction",
]


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """进程级单例 Engine（URL 参数由 core.pg.resolve_pg_params 统一裁决）。

    pool_pre_ping 每次取用前探测连接存活，防 PG 重启/超时后的陈旧连接。
    """
    params = resolve_pg_params()
    url = URL.create(
        drivername="postgresql+psycopg",
        username=params["user"],
        password=params["password"],
        host=params["host"],
        port=int(params["port"]),
        database=params["dbname"],
    )
    # connect_timeout：PG 不可达时快速失败，避免单元/冒烟在缺库环境挂死
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


@contextmanager
def read_connection(*, read_only: bool = False) -> Iterator[_SaConnection]:
    """只读/短语句连接上下文：归还自动 rollback，异常后连接状态自愈。

    Args:
        read_only: True 时设置底层会话只读（分析/看板消费侧防误写）。
            退出时必须复位：``read_only`` 是会话级参数，池 ``rollback``
            不会清掉；若不复位，后续借到同一物理连接的写事务会被拒。
    """
    with get_engine().connect() as conn:
        if not read_only:
            yield conn
            return
        raw = raw_psycopg_connection(conn)
        raw.read_only = True
        try:
            yield conn
        finally:
            raw.read_only = False


@contextmanager
def write_transaction() -> Iterator[_SaConnection]:
    """写事务上下文：成功 commit，异常 rollback（连接复位自愈）。

    COPY 与 executemany 必须在本块（或 ``read_connection`` 块）内完成，
    不得把借出的连接存到实例上跨块使用。
    """
    with get_engine().begin() as conn:
        yield conn


def raw_psycopg_connection(conn: _SaConnection) -> Any:
    """取底层 psycopg3 原生连接（COPY 逃生舱）。

    仅在 ``write_transaction()``/``read_connection()`` 块内使用——原生连接
    共享外层事务，块退出时统一 commit/rollback。返回 Any 原因：第三方
    DBAPI 连接对象，psycopg3 ``Connection``（COPY 协议为其独有能力）。
    """
    return conn.connection.driver_connection


def exec_sql(
    conn: _SaConnection,
    sql: str,
    params: Sequence[Any] | None = None,
) -> CursorResult[Any]:
    """驱动级 SQL 执行（保持 psycopg ``%s`` 占位符，SQL 字符串零改写）。

    ``exec_driver_sql`` 的列表参数语义是 executemany（元素须为元组/字典），
    与 psycopg「标量参数列表」习惯冲突。本函数按元素类型路由：
    - 标量列表 → 转元组单执行（本仓绝大多数查询）；
    - 元组/字典元素的列表 → 原样透传驱动 executemany。
    """
    if params is None:
        return conn.exec_driver_sql(sql)
    if len(params) > 0 and isinstance(params[0], tuple | dict):
        return conn.exec_driver_sql(sql, params)
    return conn.exec_driver_sql(sql, tuple(params))


def db_version() -> str:
    """返回 PostgreSQL 服务器版本（连通性自检，驱动无关入口）。"""
    with read_connection() as conn:
        return str(exec_sql(conn, "SELECT version()").scalar())
