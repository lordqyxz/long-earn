"""启动时数据缓存同步（ADR-014 阶段 G）。

架构调整：把"按需增量同步"改为"启动时一次性同步 + 后续纯缓存"。

本模块位于 ``services`` 层（编排层），协调 ``backtest.data``（数据层）和
``DataIngestionService``（同层服务）完成同步。不放在 ``backtest.data`` 下，
因为 import-linter 契约禁止数据层依赖 services。

流程：
1. 系统启动时（``initialize_context``）调用 :func:`sync_data_cache`
2. 检查 xtquant 可用性：
   - 不可用 → 跳过同步，纯缓存模式（缓存可能过期但可用）
   - 可用 → 增量同步行情+财务到 DuckDB 缓存
3. 同步完成后设置 ``LONG_EARN_CACHE_ONLY=1`` 环境变量
4. 后续所有 :class:`MiniQmtDataProvider` 调用走纯缓存分支，禁止 xtquant

设计原则：
- 同步是**幂等**的：重复调用只补齐缺失/过期部分
- 同步是**可选**的：xtquant 不可用时降级到纯缓存（可能过期）
- 同步是**显式**的：只有 :func:`sync_data_cache` 触发，不在 Provider getter 里隐式触发
- 同步后**强制纯缓存**：环境变量 ``LONG_EARN_CACHE_ONLY=1`` 让
  :class:`MiniQmtClient.is_available` 返回 False

与现有 :class:`DataIngestionService` 的关系：
- ``DataIngestionService.run(full=True)`` 是 CLI 全量下载入口（``long-earn download``），
  用于初次建仓或强制刷新
- :func:`sync_data_cache` 是运行时增量同步，内部委托 ``DataIngestionService.run``
  执行智能增量下载，同步完成后切换到纯缓存模式
- 二者都写入同一个 DuckDB 缓存，互不冲突
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from loguru import logger

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.miniqmt_provider import MiniQmtClient

if TYPE_CHECKING:
    from long_earn.services.logger_service import LoggerService

# 环境变量：设置后 MiniQmtClient.is_available 返回 False，强制走缓存分支
CACHE_ONLY_ENV = "LONG_EARN_CACHE_ONLY"


def is_cache_only() -> bool:
    """检测当前是否处于纯缓存模式。"""
    val = os.environ.get(CACHE_ONLY_ENV, "").strip().lower()
    return val in ("1", "true", "yes", "on")


def set_cache_only() -> None:
    """设置纯缓存模式环境变量。

    同时清理 :class:`MiniQmtClient` 单例的 ``_available`` 缓存，
    确保后续 ``is_available`` 重新检测时读到新的环境变量值。
    """
    os.environ[CACHE_ONLY_ENV] = "1"
    # 清理单例缓存，让下次 is_available 重新走环境变量检测分支
    client = MiniQmtClient.get()
    client._available = None
    client._xtdata = None
    logger.info(f"已设置 {CACHE_ONLY_ENV}=1，后续数据访问走纯缓存分支")


def sync_data_cache(
    universe: str = "all",
    end_date: str = "",
    skip_financial: bool = False,
    logger_service: LoggerService | None = None,
) -> dict[str, object]:
    """启动时增量同步行情+财务数据到 DuckDB 缓存。

    内部委托 :class:`DataIngestionService.run`（智能增量模式）执行实际下载，
    它已封装 staleness 检测、增量下载、缓存写入、断点续传。

    同步完成后设置 ``LONG_EARN_CACHE_ONLY=1``，后续所有数据访问走纯缓存。

    Args:
        universe: 股票池，默认 "all"（沪深A股+ETF）；支持 "all_a"/"etf"/指数代码
        end_date: 同步截止日期，空字符串默认今天
        skip_financial: 跳过财务同步（仅同步行情）
        logger_service: 可选日志服务，None 则用 loguru

    Returns:
        同步结果摘要 dict：
        - ``status``: "ok" / "skipped" / "error"
        - ``reason``: 状态说明（skipped/error 时）
        - ``ingestion``: DataIngestionService.run 返回值（ok 时）
    """
    def _log(msg: str, level: str = "info") -> None:
        if logger_service is not None:
            getattr(logger_service, level)(msg)
        else:
            getattr(logger, level)(msg)

    # 已处于纯缓存模式 → 跳过同步（避免重复）
    if is_cache_only():
        _log(f"{CACHE_ONLY_ENV}=1 已设置，跳过同步（纯缓存模式）")
        return {
            "status": "skipped",
            "reason": "cache_only_already_set",
        }

    cache = DataCache()

    _log("=" * 60)
    _log("启动时数据缓存同步")
    _log(f"股票池: {universe}, 截止日期: {end_date or '(今天)'}")
    _log("=" * 60)

    client = MiniQmtClient.get()
    if not client.is_available:
        _log("xtquant 不可用，跳过同步（降级到纯缓存模式）", "warning")
        set_cache_only()
        return {
            "status": "skipped",
            "reason": "xtquant_unavailable",
            "cache_path": str(cache.db_path),
        }

    # 委托 DataIngestionService 执行智能增量下载
    from long_earn.services.data_ingestion_service import (  # noqa: PLC0415
        DataIngestionService,
    )

    service = DataIngestionService(logger=logger_service)
    try:
        result = service.run(
            universe=universe,
            end_date=end_date,
            skip_financial=skip_financial,
            full=False,  # 智能增量模式
        )
    except Exception as exc:
        _log(f"数据同步异常: {exc}", "error")
        set_cache_only()
        return {
            "status": "error",
            "reason": f"ingestion_failed: {exc}",
            "cache_path": str(cache.db_path),
        }

    status = result.get("status", "error")
    if status != "ok":
        _log(f"数据同步未完成: {result}", "warning")
    else:
        _log(
            f"数据同步完成: 行情 {result.get('price_symbols', 0)} 只, "
            f"财务 {result.get('financial_symbols', 0)} 只"
        )
        _log(f"缓存路径: {result.get('cache_path', cache.db_path)}")

    _log("=" * 60)

    # 关键：同步完成后设置纯缓存模式（无论成功失败，都切换到纯缓存）
    # 失败时缓存可能不完整，但后续走纯缓存避免 worker 触发 xtquant 崩溃
    set_cache_only()

    with __import__("contextlib").suppress(Exception):
        cache.close()

    return {
        "status": status,
        "ingestion": result,
    }
