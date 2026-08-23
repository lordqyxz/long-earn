"""merged panel 跨 run 磁盘缓存（Arrow IPC）。

同一 ``(symbols, start, end)`` 参数的合并面板在参数网格寻优 / 并行
回测场景下被反复构建（PG 拉数 + 合并排序），是跨 run 重复开销。此处
以 Arrow IPC 文件落盘复用：

- key = sha256(排序后 symbols + start + end)——symbols 顺序不敏感；
- 命中直接读文件，未命中构建后原子写（临时文件 + os.replace，
  多进程同时写同一 key 时不会留下半截文件）；
- 缓存文件损坏（读失败）回退 provider 重建并覆写；
- 空面板不写缓存（避免把「无数据」固化为缓存结果）。

默认关闭（引擎 ``enable_panel_cache`` 开关），由寻优 / 并行编排显式
开启；PG 价格数据在回测期间不主动变更（缓存保护约定），key 语义安全。
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Protocol

import polars as pl
from loguru import logger

from long_earn.core.storage import panel_cache_dir

_MAX_CACHE_FILES = 128
"""缓存目录文件数上限：超出按 mtime 淘汰最旧。网格寻优换日期窗口会
持续产生新 (symbols, start, end) 组合，无上限会吃满磁盘。"""

_STALE_TMP_SECONDS = 3600
"""tmp 残留判定阈值：mtime 早于此的 tmp 视为崩溃遗留可清理；
更新鲜的 tmp 可能是并发写者正持有，不动。"""


class _PanelProvider(Protocol):
    """合并面板构建方契约（DataConnector.get_merged_panel_as_polars）。"""

    def get_merged_panel_as_polars(
        self, symbols: list[str], start: str, end: str
    ) -> pl.DataFrame: ...


def _cache_dir() -> Path:
    """缓存目录（测试经 monkeypatch 重定向隔离共享存储）。"""
    return panel_cache_dir()


def _cache_key(symbols: list[str], start: str, end: str) -> str:
    """canonical 参数指纹：symbols 排序去敏 + 区间。"""
    canonical = "|".join(sorted(symbols)) + f"#{start}#{end}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _clean_stale_tmp(path: Path) -> None:
    """清理同 key 的陈旧 tmp 残留（进程在 write_ipc 与 replace 间崩溃遗留）。"""
    now = time.time()
    for tmp_file in path.parent.glob(f"{path.stem}.*.tmp"):
        try:
            if now - tmp_file.stat().st_mtime > _STALE_TMP_SECONDS:
                tmp_file.unlink()
        except OSError:
            pass  # 竞争删除（另一进程先删）无害


def _evict_old_files(cache_dir: Path) -> None:
    """缓存文件数超 ``_MAX_CACHE_FILES`` 时按 mtime 淘汰最旧。"""

    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            # 竞争删除（另一进程先 unlink）：排最旧端，后续 unlink 同样吞 OSError
            return 0.0

    # stat 失败必须吞：本函数在 _atomic_write_ipc 的写缓存 try 块之外，
    # 多进程并行回测竞争删除文件时抛 OSError 会让缓存已写成功的回测崩溃
    files = sorted(cache_dir.glob("*.arrow"), key=_mtime, reverse=True)
    for stale in files[_MAX_CACHE_FILES:]:
        try:
            stale.unlink()
            logger.debug(
                f"[panel_cache] 淘汰旧缓存（超 {_MAX_CACHE_FILES} 上限）: {stale.name}"
            )
        except OSError:
            pass  # 竞争删除无害


def _atomic_write_ipc(df: pl.DataFrame, path: Path) -> None:
    """临时文件写入后原子替换（多进程写同一 key 安全）。"""
    _clean_stale_tmp(path)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        df.write_ipc(tmp)
        os.replace(tmp, path)
    except Exception:
        # 写失败不影响正确性（下次未命中重建），只清理残留临时文件
        logger.warning(f"[panel_cache] 缓存写入失败，跳过: {path}")
        tmp.unlink(missing_ok=True)
        return
    _evict_old_files(path.parent)


def cached_merged_panel(
    provider: _PanelProvider,
    symbols: list[str],
    start: str,
    end: str,
) -> pl.DataFrame:
    """带跨 run 磁盘缓存的合并面板获取。

    Args:
        provider: 面板构建方（引擎 data_provider）。
        symbols: 标的列表（顺序不敏感）。
        start: 起始日期 ``YYYY-MM-DD``。
        end: 结束日期 ``YYYY-MM-DD``。

    Returns:
        合并面板；空面板原样返回不缓存（契约：空数据返回空 DataFrame）。

    失效约定：PG 数据更新（``scripts/download_data.py``）后由脚本清空
    ``panel_cache/`` 目录——key 不感知底层数据变更，陈旧缓存命中是数据
    正确性问题。
    """
    path = _cache_dir() / f"{_cache_key(symbols, start, end)}.arrow"
    if path.exists():
        try:
            df = pl.read_ipc(path)
            logger.debug(f"[panel_cache] 命中: {len(symbols)} 只 {start}~{end}")
            return df
        except Exception as exc:
            logger.warning(f"[panel_cache] 缓存损坏，回退重建: {path} ({exc})")

    panel = provider.get_merged_panel_as_polars(symbols, start, end)
    if not panel.is_empty():
        _atomic_write_ipc(panel, path)
    return panel
