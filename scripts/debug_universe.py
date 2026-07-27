"""调试 universe 缓存日期范围。"""
from __future__ import annotations

import duckdb
from long_earn.core.storage import backtest_cache_path


def main() -> None:
    conn = duckdb.connect(str(backtest_cache_path()), read_only=True)
    for code in ("中证500", "csi500", "沪深300", "csi300", "all_a", "沪深A股"):
        r = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(DISTINCT date) "
            "FROM universe_constituents WHERE index_code = ?",
            [code],
        ).fetchone()
        print(f"{code}: min={r[0]}, max={r[1]}, distinct_dates={r[2]}")
    conn.close()


if __name__ == "__main__":
    main()
