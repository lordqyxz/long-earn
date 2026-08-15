"""调试 universe 缓存日期范围（PostgreSQL 版）。"""
from __future__ import annotations

from long_earn.core.pg import pg_connect


def main() -> None:
    conn = pg_connect(read_only=True)
    try:
        for code in ("中证500", "csi500", "沪深300", "csi300", "all_a", "沪深A股"):
            r = conn.execute(
                "SELECT MIN(date), MAX(date), COUNT(DISTINCT date) "
                "FROM universe_constituents WHERE index_code = %s",
                [code],
            ).fetchone()
            print(f"{code}: min={r[0]}, max={r[1]}, distinct_dates={r[2]}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
