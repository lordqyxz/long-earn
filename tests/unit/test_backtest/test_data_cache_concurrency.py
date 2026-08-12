"""DataCache 进程内单写者与事务边界测试。"""

from __future__ import annotations

import threading
from pathlib import Path

import pandas as pd

from long_earn.backtest.data.cache import DataCache


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


def test_cache_instances_serialize_writes_to_same_temporary_database(
    tmp_path: Path,
) -> None:
    """不同实例/线程写同一路径应串行提交且不丢记录。"""

    db_path = tmp_path / "concurrent.duckdb"
    caches = [DataCache(db_path), DataCache(db_path)]
    errors: list[Exception] = []

    def writer(index: int) -> None:
        try:
            for day in range(1, 11):
                caches[index].save_prices(_price_frame(f"TEST{index}", day))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    with DataCache(db_path) as reader:
        rows = reader.get_prices(["TEST0", "TEST1"], "2026-01-01", "2026-01-10")
    assert rows is not None
    assert len(rows) == 20
