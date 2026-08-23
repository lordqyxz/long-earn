"""merged panel 跨 run 磁盘缓存契约测试（Arrow IPC）。

验证目标：
1. 同 (symbols, start, end) 参数第二次调用命中缓存，不重建面板；
2. 缓存文件损坏时回退 provider 重建并覆写缓存；
3. 不同参数不共享缓存条目。

单测全程 Mock provider + tmp_path 缓存目录，不触碰共享存储。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import polars as pl
import pytest

from long_earn.backtest.data.panel_cache import cached_merged_panel


def _panel(close_a: float = 100.0, close_b: float = 101.0) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "timestamp": [
                datetime(2024, 1, 1),
                datetime(2024, 1, 2),
            ],
            "symbol": ["600519.SH", "600519.SH"],
            "close": [close_a, close_b],
        }
    )


class _CountingProvider:
    """假 provider：计数真实构建次数，返回固定面板。"""

    def __init__(self, panel: pl.DataFrame | None = None) -> None:
        self.build_count = 0
        self._panel = panel if panel is not None else _panel()

    def get_merged_panel_as_polars(
        self, symbols: list[str], start: str, end: str
    ) -> pl.DataFrame:
        self.build_count += 1
        return self._panel


@pytest.fixture
def cache_dir(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """把缓存目录重定向到 tmp_path，隔离共享存储。"""
    monkeypatch.setattr(
        "long_earn.backtest.data.panel_cache._cache_dir", lambda: tmp_path
    )
    return tmp_path


def test_second_call_hits_cache(
    cache_dir: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同参数第二次调用直接读缓存，provider 只构建一次。"""
    provider = _CountingProvider()

    p1 = cached_merged_panel(provider, ["600519.SH"], "2024-01-01", "2024-12-31")
    p2 = cached_merged_panel(provider, ["600519.SH"], "2024-01-01", "2024-12-31")

    assert provider.build_count == 1
    assert p1.equals(p2)


def test_symbols_order_insensitive(cache_dir: Any) -> None:
    """symbols 顺序不同视为同一 key（canonical 排序）。"""
    provider = _CountingProvider()

    cached_merged_panel(provider, ["A", "B"], "2024-01-01", "2024-12-31")
    cached_merged_panel(provider, ["B", "A"], "2024-01-01", "2024-12-31")

    assert provider.build_count == 1


def test_corrupt_cache_falls_back(cache_dir: Any) -> None:
    """缓存文件损坏时回退 provider 重建并覆写缓存文件。"""
    provider = _CountingProvider()
    cached_merged_panel(provider, ["600519.SH"], "2024-01-01", "2024-12-31")

    # 破坏缓存文件
    arrow_files = list(cache_dir.glob("*.arrow"))
    assert len(arrow_files) == 1
    arrow_files[0].write_bytes(b"corrupted")

    p = cached_merged_panel(provider, ["600519.SH"], "2024-01-01", "2024-12-31")
    assert provider.build_count == 2
    assert p.equals(_panel())

    # 缓存已被覆写为合法内容，第三次调用重新命中
    cached_merged_panel(provider, ["600519.SH"], "2024-01-01", "2024-12-31")
    assert provider.build_count == 2


def test_different_params_no_collision(cache_dir: Any) -> None:
    """不同区间参数各自构建，不共享缓存条目。"""
    provider = _CountingProvider()

    cached_merged_panel(provider, ["600519.SH"], "2024-01-01", "2024-06-30")
    cached_merged_panel(provider, ["600519.SH"], "2024-01-01", "2024-12-31")

    assert provider.build_count == 2
    assert len(list(cache_dir.glob("*.arrow"))) == 2


def test_empty_panel_not_cached(cache_dir: Any) -> None:
    """空面板不写缓存（下次调用仍走 provider，避免缓存空结果）。"""
    provider = _CountingProvider(panel=_panel().head(0))

    cached_merged_panel(provider, ["600519.SH"], "2024-01-01", "2024-12-31")
    cached_merged_panel(provider, ["600519.SH"], "2024-01-01", "2024-12-31")

    assert provider.build_count == 2
    assert len(list(cache_dir.glob("*.arrow"))) == 0


def test_cache_evicts_oldest_beyond_limit(
    cache_dir: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缓存目录文件数超上限时按 mtime 淘汰最旧（磁盘有界）。"""
    from long_earn.backtest.data import panel_cache as pc

    monkeypatch.setattr(pc, "_MAX_CACHE_FILES", 5)
    provider = _CountingProvider()

    # 依次写入 7 个不同 key（mtime 递增），上限 5 → 最旧 2 个被淘汰
    for month in range(1, 8):
        end = f"2024-{month:02d}-28"
        cached_merged_panel(provider, ["600519.SH"], "2024-01-01", end)

    files = sorted(cache_dir.glob("*.arrow"), key=lambda p: p.stat().st_mtime)
    assert len(files) == 5  # 上限生效
    # 最旧的 2 个（month=1/2 的 key）已不在目录中
    remaining_keys = {f.name for f in files}
    for month in range(1, 8):
        key = pc._cache_key(["600519.SH"], "2024-01-01", f"2024-{month:02d}-28")
        if month <= 2:
            assert f"{key}.arrow" not in remaining_keys
        else:
            assert f"{key}.arrow" in remaining_keys


def test_stale_tmp_cleaned_on_write(
    cache_dir: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写入前清理陈旧 tmp 残留（进程崩溃遗留），新鲜 tmp（并发写者）不动。"""
    import os
    import time

    from long_earn.backtest.data import panel_cache as pc

    provider = _CountingProvider()
    key = pc._cache_key(["600519.SH"], "2024-01-01", "2024-12-31")

    # 伪造两个残留 tmp：一个陈旧（2 小时前）、一个新鲜（并发写者正持有）
    stale = cache_dir / f"{key}.11111.tmp"
    fresh = cache_dir / f"{key}.22222.tmp"
    stale.write_bytes(b"stale")
    fresh.write_bytes(b"fresh")
    old_ts = time.time() - 7200
    os.utime(stale, (old_ts, old_ts))

    cached_merged_panel(provider, ["600519.SH"], "2024-01-01", "2024-12-31")

    assert not stale.exists()  # 陈旧 tmp 被清理
    assert fresh.exists()  # 新鲜 tmp 不被误删（可能是并发写者）
    assert (cache_dir / f"{key}.arrow").exists()  # 正常缓存写入
