"""core.db 引擎层契约：只读拒写、写事务回滚、异常后池连接可再用。"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import DBAPIError

from long_earn.core.db import (
    db_version,
    exec_sql,
    read_connection,
    write_transaction,
)

pytestmark = pytest.mark.integration

_PROBE = "_le_core_db_l2_probe"


@pytest.fixture
def probe_table() -> None:
    """隔离探测表：不碰价格/审计/物质等权威表。"""
    with write_transaction() as conn:
        exec_sql(
            conn,
            f"CREATE TABLE IF NOT EXISTS {_PROBE} (id integer PRIMARY KEY)",
        )
        exec_sql(conn, f"DELETE FROM {_PROBE}")
    yield
    with write_transaction() as conn:
        exec_sql(conn, f"DROP TABLE IF EXISTS {_PROBE}")


def test_db_version_reachable() -> None:
    """连通性自检返回 PostgreSQL 版本串。"""
    version = db_version()
    assert "PostgreSQL" in version


def test_read_only_rejects_insert(probe_table: None) -> None:
    """read_only 会话拒绝 INSERT，防止分析侧误写。"""
    with read_connection(read_only=True) as conn, pytest.raises(DBAPIError):
        exec_sql(conn, f"INSERT INTO {_PROBE} (id) VALUES (1)")


def test_read_only_does_not_stick_on_pooled_connection(probe_table: None) -> None:
    """read_only 退出后复位：后续写事务仍可 INSERT（防池连接会话粘滞）。"""
    with read_connection(read_only=True) as conn:
        exec_sql(conn, f"SELECT COUNT(*) FROM {_PROBE}").scalar()

    with write_transaction() as conn:
        exec_sql(conn, f"INSERT INTO {_PROBE} (id) VALUES (42)")
        count = exec_sql(conn, f"SELECT COUNT(*) FROM {_PROBE}").scalar()
    assert count == 1


def test_write_transaction_rolls_back_on_error(probe_table: None) -> None:
    """写事务块内未捕获异常时整段回滚。"""
    with pytest.raises(RuntimeError, match="boom"), write_transaction() as conn:
        exec_sql(conn, f"INSERT INTO {_PROBE} (id) VALUES (1)")
        raise RuntimeError("boom")

    with read_connection() as conn:
        count = exec_sql(conn, f"SELECT COUNT(*) FROM {_PROBE}").scalar()
    assert count == 0


def test_sql_error_does_not_poison_pool(probe_table: None) -> None:
    """语句失败归还后，下一笔写事务仍可用（替代手工 rollback 自愈）。"""
    with pytest.raises(DBAPIError), write_transaction() as conn:
        exec_sql(conn, "SELECT 1 FROM _le_core_db_nonexistent_xyz")

    with write_transaction() as conn:
        exec_sql(conn, f"INSERT INTO {_PROBE} (id) VALUES (1)")
        count = exec_sql(conn, f"SELECT COUNT(*) FROM {_PROBE}").scalar()
    assert count == 1


def test_exec_sql_executemany_list_of_tuples(probe_table: None) -> None:
    """元组列表走 executemany，一次写入多行。"""
    rows = [(1,), (2,), (3,)]
    with write_transaction() as conn:
        exec_sql(conn, f"INSERT INTO {_PROBE} (id) VALUES (%s)", rows)
    with read_connection() as conn:
        count = exec_sql(conn, f"SELECT COUNT(*) FROM {_PROBE}").scalar()
    assert count == 3
