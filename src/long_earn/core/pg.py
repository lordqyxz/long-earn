"""PostgreSQL 统一连接工具（审计/缓存/物质库共用的单一连接入口）。

全量迁移 PostgreSQL 后，所有持久化存储（回测审计日志、价格/财务缓存、
物质记忆库）统一走本模块提供的连接工厂。

**单一真相源约定**（与 ``core/storage.py`` 同构）：
- 默认连接参数常量（``DEFAULT_PG_*``）**只定义在本模块**；
- ``AppConfig`` 的 ``pg_*`` 字段从本模块**派生**（默认值引用常量，
  ``from_env`` 显式读 env 后投影），不在 config 重复字面量；
- 各业务模块无参调用 ``pg_connect()`` 时，本模块兜底读 env + 默认值。

约定：
- 读写连接：``pg_connect()``，事务由调用方显式管理；
- 只读连接：``pg_connect(read_only=True)``（连接建立后设置
  ``conn.read_only = True`` 防止误写；审计/分析等消费侧一律只读，
  遵循单写者纪律。注意 psycopg3 的 conninfo 解析器不接受
  ``default_transaction_read_only`` 作为连接选项，必须走属性设置）；
- 所有连接默认 ``autocommit=False``，调用方负责 commit/rollback 与 close。
"""

from __future__ import annotations

import os
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

# 默认连接参数 — 唯一真相源（AppConfig 从本模块派生，不重复定义）
DEFAULT_PG_HOST = "127.0.0.1"
DEFAULT_PG_PORT = 5432
DEFAULT_PG_DB = "long_earn"
DEFAULT_PG_USER = "postgres"
DEFAULT_PG_PASSWORD = "postgres"


def resolve_pg_params() -> dict[str, str]:
    """裁决 PostgreSQL 连接参数（env → 默认值）。

    与 ``core.storage.resolve_paths`` 同构：本模块是连接参数的唯一裁决者。
    ``AppConfig.from_env`` 调用本函数投影到 ``pg_*`` 字段；业务模块无参
    调用 ``pg_connect`` 时也经本函数兜底。

    Returns:
        ``{host, port, dbname, user, password}`` 参数字典
    """
    return {
        "host": os.getenv("PG_HOST", DEFAULT_PG_HOST),
        "port": os.getenv("PG_PORT", str(DEFAULT_PG_PORT)),
        "dbname": os.getenv("PG_DB", DEFAULT_PG_DB),
        "user": os.getenv("PG_USER", DEFAULT_PG_USER),
        "password": os.getenv("PG_PASSWORD", DEFAULT_PG_PASSWORD),
    }


def _apply_overrides(
    params: dict[str, str], overrides: dict[str, str] | None
) -> dict[str, str]:
    """把调用方覆盖项合入连接参数（只覆盖非空值）。"""
    if not overrides:
        return params
    merged = dict(params)
    for key, value in overrides.items():
        if value:
            merged[key] = str(value)
    return merged


def pg_conninfo(
    *,
    overrides: dict[str, str] | None = None,
) -> str:
    """构造 psycopg 连接串（keyword=value 形式）。

    Args:
        overrides: 覆盖连接参数（键为 host/port/dbname/user/password，
            空值忽略；不传则全用 resolve_pg_params 的 env/默认值）
    """
    params = _apply_overrides(resolve_pg_params(), overrides)
    return " ".join(f"{k}={v}" for k, v in params.items())


def pg_connect(
    *,
    overrides: dict[str, str] | None = None,
    read_only: bool = False,
    row_factory: Any = dict_row,
    autocommit: bool = False,
) -> psycopg.Connection:
    """打开 PostgreSQL 连接（读写或只读）。

    Args:
        overrides: 覆盖连接参数（键为 host/port/dbname/user/password，
            空值忽略；不传则全用 resolve_pg_params 的 env/默认值）
        read_only: True 时强制只读会话（连接建立后设置
            ``conn.read_only``，避免误写）
        row_factory: 行工厂（默认 dict_row；传 None 取元组行）
        autocommit: True 时每条语句自动提交，不持有未提交事务——
            适合只读/高频查询（避免 MVCC 快照长时间占用，阻塞
            其它连接的 DDL 如 ``ALTER TABLE``）；显式 ``BEGIN``/
            ``COMMIT`` 仍可组合成多语句事务

    Returns:
        已打开的 psycopg Connection（默认 autocommit=False，调用方负责
        commit/rollback 与 close）
    """
    conn = psycopg.connect(pg_conninfo(overrides=overrides), autocommit=autocommit)
    if read_only:
        conn.read_only = True
    if row_factory is not None:
        conn.row_factory = row_factory
    return conn


def ensure_database(dbname: str = "") -> None:
    """确保目标数据库存在（连接默认库 postgres 建库）。

    Docker 首次启动或全新环境时调用；幂等（已存在则跳过）。
    """
    target = dbname or resolve_pg_params()["dbname"]
    admin = psycopg.connect(
        pg_conninfo(overrides={"dbname": "postgres"}), autocommit=True
    )
    try:
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (target,)
        ).fetchone()
        if not exists:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target))
            )
    finally:
        admin.close()


def pg_version() -> str:
    """返回 PostgreSQL 服务器版本（连通性自检用）。"""
    conn = pg_connect(read_only=True)
    try:
        row = conn.execute("SELECT version()").fetchone()
        if row is None:
            return ""
        if isinstance(row, dict):
            return str(row.get("version", ""))
        return str(row[0])
    finally:
        conn.close()
