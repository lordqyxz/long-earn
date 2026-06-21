"""测试根 conftest：跨目录共享的测试辅助。

pytest 自动发现本文件并应用到所有测试，无需把 tests/ 变成可导入包。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import pytest


class MockDataProvider:
    """模拟数据提供者：按 timestamp/symbol 过滤面板（多份 e2e 测试复用）。"""

    def __init__(self, panel: pl.DataFrame) -> None:
        self._panel = panel

    def get_merged_panel_as_polars(self, symbols: list[str], start: str, end: str) -> pl.DataFrame:
        return self._panel.filter(
            (pl.col("symbol").is_in(symbols))
            & (pl.col("timestamp") >= datetime.strptime(start, "%Y-%m-%d"))
            & (pl.col("timestamp") <= datetime.strptime(end, "%Y-%m-%d"))
        )


@pytest.fixture
def mock_data_provider():
    """返回 MockDataProvider 类，供测试按需实例化。"""
    return MockDataProvider


@pytest.fixture
def small_causality_panel() -> pl.DataFrame:
    """2 symbol × 30 日的确定性小面板，供因果性证明复用。"""
    rows = []
    base = datetime(2024, 1, 1)
    for i in range(30):
        ts = base + timedelta(days=i)
        for s_idx, sym in enumerate(["A.SZ", "B.SH"]):
            t = i + 1
            close = 10.0 + s_idx * 2 + 0.3 * t + (t % 4)
            rows.append(
                {
                    "timestamp": ts, "symbol": sym,
                    "close": round(close, 4),
                    "high": close + 0.1, "low": close - 0.1,
                }
            )
    return pl.DataFrame(rows)
