"""SQLAlchemy 2.0 Core 引擎层 — 数据底座的统一连接/事务入口（2026-08-30）。

数据库操作稳定化第一阶段（ADR-021 后续工程决策）：DataCache 等批量分析型
负载迁移到 SQLAlchemy Core（仅 Core，不用 ORM——COPY 百万行装载、polars/
pandas 直读的负载与 ORM 会话模型冲突）。连接参数仍由 ``core.pg`` 单一裁决，
本模块只负责引擎生命周期与连接/事务语义的统一。

设计要点：
- **进程级单例 Engine**：池化替代旧「DataCache 线程局部连接 / 审计实例连接 /
  分析器短连接」三种分叉模式。Engine 不可跨进程 pickle——spawn worker 子进程
  首次调用时惰性自建（与「worker 各自建连」的既有纪律一致）。
- **读路径**：``read_connection()`` 上下文，连接归还自动 rollback——单条语句
  失败后连接状态由池复位，天然消除 psycopg3 aborted-transaction 中毒
  （替代手工自愈；旧 autocommit 读的「不持长事务」语义由短上下文等价实现）。
- **写路径**：``write_transaction()`` 上下文（engine.begin() 语义）：成功
  commit / 异常 rollback。调用方的进程内单写者 RLock 与 ``pg_advisory_xact_lock``
  纪律原样保留在块内（应用层逻辑，与驱动无关）。
- **COPY 逃生舱**：SQLAlchemy 无 COPY 抽象；``raw_psycopg_connection()``
  下沉到 psycopg3 原生连接，``save_prices`` 等批量装载路径使用（共享外层
  事务，块退出统一 commit/rollback，脏标记原子性保持）。
- **SQL 执行风格**：DataCache 经 ``exec_driver_sql`` 保持 psycopg ``%s``
  占位符与列表/元组参数不变（全仓 SQL 字符串零改写）；行消费走 Row 的
  整数下标（与 DuckDB 时代 fetchall 元组契约兼容）。
- **DDL 策略**：维持「构造即建表」幂等 DDL（13 个 PG 测试文件的依赖假设），
  不引入 alembic。

第二阶段（未做）：审计 ``PostgresAuditProvider`` / ``substance.persistence`` /
``app.analyzer`` 迁移到本模块。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from sqlalchemy import URL, Connection as _SaConnection, Engine, create_engine

from long_earn.core.pg import resolve_pg_params

__all__ = [
    "db_version",
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
    return create_engine(url, pool_pre_ping=True)


@contextmanager
def read_connection() -> Iterator[_SaConnection]:
    """只读/短语句连接上下文：归还自动 rollback，异常后连接状态自愈。"""
    with get_engine().connect() as conn:
        yield conn


@contextmanager
def write_transaction() -> Iterator[_SaConnection]:
    """写事务上下文：成功 commit，异常 rollback（连接复位自愈）。"""
    with get_engine().begin() as conn:
        yield conn


def raw_psycopg_connection(conn: _SaConnection) -> Any:
    """取底层 psycopg3 原生连接（COPY 逃生舱）。

    仅在 ``write_transaction()``/``read_connection()`` 块内使用——原生连接
    共享外层事务，块退出时统一 commit/rollback。返回 Any 原因：第三方
    DBAPI 连接对象，psycopg3 ``Connection``（COPY 协议为其独有能力）。
    """
    return conn.connection.driver_connection


def db_version() -> str:
    """返回 PostgreSQL 服务器版本（连通性自检，驱动无关入口）。"""
    with read_connection() as conn:
        return str(conn.exec_driver_sql("SELECT version()").scalar())
