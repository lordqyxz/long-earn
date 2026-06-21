"""算子测试共享 fixture：确定性面板。MockDataProvider / small_causality_panel
在 tests/conftest.py（跨目录复用）。"""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import pytest


@pytest.fixture
def panel() -> pl.DataFrame:
    """3 symbol × 40 日的确定性面板。

    close 用确定性公式（含趋势 + 周期），避免随机种子；volume 用线性递增。
    数据故意不按 (symbol,timestamp) 排序，以检验算子内部排序对齐的正确性。
    """

    rows = []
    base = datetime(2024, 1, 1)
    symbols = ["000001.SZ", "600000.SH", "300750.SZ"]
    for i in range(40):
        ts = base + timedelta(days=i)
        for s_idx, sym in enumerate(symbols):
            t = i + 1
            close = 10.0 + s_idx * 5 + 0.5 * t + 2.0 * (t % 7) - 0.01 * (t**2 % 13)
            rows.append(
                {
                    "timestamp": ts, "symbol": sym,
                    "open": close - 0.1, "high": close + 0.3, "low": close - 0.3,
                    "close": round(close, 4),
                    "volume": 1000.0 * (t + s_idx * 10),
                }
            )
    return pl.DataFrame(rows).sample(fraction=1.0, shuffle=True, seed=42)
