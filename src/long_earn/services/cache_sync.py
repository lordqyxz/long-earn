"""数据主库同步：本地 DuckDB 优先，按需从 miniQMT 增量补齐。

本模块位于 ``services`` 层（编排层），协调 ``backtest.data``（数据层）和
``IncrementalSyncService``（同层服务）完成同步。不放在 ``backtest.data`` 下，
因为 import-linter 契约禁止数据层依赖 services。

数据策略（缓存优先 + 自动更新）::

1. **读路径（常态）**：:class:`MiniQmtDataProvider` 先读 DuckDB 主数据层；
   缺失 / 过期时若 miniqmt 可用则增量下载并写回缓存。
2. **启动时机**：:func:`sync_data_cache` 对股票池做一次智能增量批量同步
   （缺什么补什么），**不再**事后强制 ``LONG_EARN_CACHE_ONLY``，
   以便运行期仍可按需从 miniqmt 补洞。
3. **并行 worker**：:mod:`parallel` 在子进程内临时
   ``LONG_EARN_DISABLE_XTQUANT``，避免 xtquant C++ 崩溃；主进程可先刷新再共享内存。
4. **显式纯缓存**：仅当用户 / CI 设置 ``LONG_EARN_CACHE_ONLY=1`` 时锁定只读缓存。

与 :class:`IncrementalSyncService` 的关系：
- ``IncrementalSyncService.sync(full=True)`` — CLI 显式同步（``long-earn sync``）
- :func:`sync_data_cache` — 启动时智能增量，委托同一 ingestion 服务
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from loguru import logger

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.miniqmt_provider import MiniQmtClient

if TYPE_CHECKING:
    from long_earn.services.logger_service import LoggerService

# 环境变量：显式设置后 MiniQmtClient.is_available 返回 False，强制只读缓存
CACHE_ONLY_ENV = "LONG_EARN_CACHE_ONLY"

# PG 迁移后无本地缓存文件路径：DataCache 的 db_path 已废弃，
# 报告里的 cache_path 统一用 PG 缓存标识（兼容旧调用方读取该字段）
_PG_CACHE_LABEL = "pg://long_earn"


def is_cache_only() -> bool:
    """检测当前是否处于显式纯缓存模式。"""
    val = os.environ.get(CACHE_ONLY_ENV, "").strip().lower()
    return val in ("1", "true", "yes", "on")


def set_cache_only() -> None:
    """显式锁定纯缓存模式（CI / 无 QMT / 用户主动要求）。

    同时清理 :class:`MiniQmtClient` 单例的 ``_available`` 缓存，
    确保后续 ``is_available`` 重新检测时读到新的环境变量值。
    """
    os.environ[CACHE_ONLY_ENV] = "1"
    client = MiniQmtClient.get()
    client._available = None
    client._xtdata = None
    logger.info(f"已设置 {CACHE_ONLY_ENV}=1，后续数据访问走纯缓存分支")


def clear_cache_only() -> None:
    """解除纯缓存锁定，恢复「缓存优先 + 可按需拉 miniqmt」。"""
    os.environ.pop(CACHE_ONLY_ENV, None)
    client = MiniQmtClient.get()
    client._available = None
    # 保留 _xtdata，避免无谓重复 import；下次 is_available 会重检环境变量
    logger.info(f"已清除 {CACHE_ONLY_ENV}，允许按需从 miniqmt 增量更新")


def sync_data_cache(
    universe: str = "all",
    end_date: str = "",
    skip_financial: bool = False,
    logger_service: LoggerService | None = None,
) -> dict[str, object]:
    """启动时增量同步行情+财务到 DuckDB（合适的批量更新时机）。

    内部委托 :class:`IncrementalSyncService.sync`（智能增量）：只补缺失/过期。
    同步完成后**保持** miniqmt 可用，以便后续读面板时仍可按需补洞。

    Args:
        universe: 股票池，默认 "all"（沪深A股+ETF）
        end_date: 同步截止日期，空=今天
        skip_financial: 跳过财务同步
        logger_service: 可选日志服务

    Returns:
        ``status``: "ok" / "skipped" / "error"；成功时含 ``ingestion``
    """

    def _log(msg: str, level: str = "info") -> None:
        if logger_service is not None:
            getattr(logger_service, level)(msg)
        else:
            getattr(logger, level)(msg)

    if is_cache_only():
        _log(f"{CACHE_ONLY_ENV}=1 已设置，跳过启动同步（显式纯缓存）")
        return {
            "status": "skipped",
            "reason": "cache_only_already_set",
        }

    cache = DataCache()

    _log("=" * 60)
    _log("启动时数据缓存同步（缓存优先；完成后仍允许按需 miniqmt 更新）")
    _log(f"股票池: {universe}, 截止日期: {end_date or '(今天)'}")
    _log("=" * 60)

    client = MiniQmtClient.get()
    if not client.is_available:
        _log(
            "xtquant 不可用，跳过启动同步；读路径将仅使用已有 DuckDB 缓存",
            "warning",
        )
        with __import__("contextlib").suppress(Exception):
            cache.close()
        return {
            "status": "skipped",
            "reason": "xtquant_unavailable",
            "cache_path": _PG_CACHE_LABEL,
        }

    from long_earn.services.incremental_sync import (  # noqa: PLC0415
        IncrementalSyncService,
    )

    service = IncrementalSyncService(logger=logger_service)
    try:
        report = service.sync(
            universe=universe,
            end_date=end_date,
            skip_financial=skip_financial,
            full=False,
        )
    except Exception as exc:
        _log(f"数据同步异常: {exc}", "error")
        with __import__("contextlib").suppress(Exception):
            cache.close()
        return {
            "status": "error",
            "reason": f"ingestion_failed: {exc}",
            "cache_path": _PG_CACHE_LABEL,
        }

    result = report.as_dict()
    status = report.status
    if status != "ok":
        _log(f"数据同步未完成: {result}", "warning")
    else:
        _log(
            f"数据同步完成: 行情 {result.get('price_symbols', 0)} 只, "
            f"财务 {result.get('financial_symbols', 0)} 只"
        )
        _log(f"缓存路径: {result.get('cache_path', _PG_CACHE_LABEL)}")

    _log("=" * 60)
    _log("策略: DuckDB 缓存优先；缺失/过期时 Provider 自动从 miniqmt 增量补齐")

    with __import__("contextlib").suppress(Exception):
        cache.close()

    return {
        "status": status,
        "ingestion": result,
    }
