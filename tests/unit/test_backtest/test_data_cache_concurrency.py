"""DataCache 进程内单写者与事务边界测试（PostgreSQL 版）。

PG 不可达时整组跳过（Docker 启动后自动恢复运行）。
"""

from __future__ import annotations

import threading
from uuid import uuid4

import pandas as pd
import pytest

from long_earn.backtest.data.cache import DataCache
from long_earn.core.pg import pg_version


def _pg_available() -> bool:
    """探测 PostgreSQL 是否可连（不可达时测试组整体跳过）。"""
    try:
        pg_version()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(), reason="PostgreSQL 服务不可用"
)


def _price_frame(symbol: str, day: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": [symbol],
            "date": [f"2026-01-{day:02d}"],
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.5],
            "volume": [1000.0],
        }
    )


def test_cache_instances_serialize_writes_to_same_postgres() -> None:
    """不同实例/线程写同一 PostgreSQL 库应串行提交且不丢记录。

    PG 全量迁移后：DataCache 忽略 db_path，统一连 PG 主库；进程内按锁
    命名空间共享 RLock 串行化写事务（跨实例）。用唯一 symbol 隔离数据。
    """
    run_tag = uuid4().hex[:10]
    caches = [DataCache(), DataCache()]
    errors: list[Exception] = []

    def writer(index: int) -> None:
        try:
            for day in range(1, 11):
                caches[index].save_prices(_price_frame(f"TEST{index}-{run_tag}", day))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    try:
        with DataCache() as reader:
            rows = reader.get_prices(
                [f"TEST0-{run_tag}", f"TEST1-{run_tag}"],
                "2026-01-01",
                "2026-01-10",
            )
        assert rows is not None
        assert len(rows) == 20
    finally:
        # 清理测试数据（避免污染 PG 共享库）
        cache = DataCache()
        try:
            conn = cache._get_conn()
            for i in range(2):
                conn.execute(
                    "DELETE FROM price_daily WHERE symbol = %s",
                    [f"TEST{i}-{run_tag}"],
                )
            conn.commit()
        finally:
            cache.close()
