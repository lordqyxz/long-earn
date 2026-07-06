"""数据下载服务 — 行情与财务数据批量入库。

从 scripts/download_data.py 抽取的核心业务逻辑，供 CLI / Web 等入口复用。
依赖 MiniQMT 客户端（xtquant）与 DuckDB 缓存。
"""

from __future__ import annotations

import contextlib
import time
from datetime import date
from typing import TYPE_CHECKING, Any

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.miniqmt_provider import (
    MiniQmtClient,
    MiniQmtDataProvider,
    MiniQmtUniverseProvider,
)

if TYPE_CHECKING:
    from long_earn.services import LoggerService

# 分批下载，避免 xtquant 单次请求过大超时
BATCH_SIZE = 50

# 全量下载的板块名
SECTOR_ALL_A = "沪深A股"
SECTOR_ALL_ETF = "沪深ETF"


class DataIngestionService:
    """数据下载服务。

    封装行情/财务数据的批量下载与入库逻辑，与 CLI 表现层解耦。
    """

    def __init__(self, logger: "LoggerService | None" = None) -> None:
        self.logger = logger
        self.cache = DataCache()
        self.data_provider = MiniQmtDataProvider(self.cache)
        self.client = MiniQmtClient.get()

    @property
    def is_available(self) -> bool:
        """MiniQMT 客户端是否可用。"""
        return self.data_provider.is_available

    def get_universe_symbols(
        self,
        universe: str,
        date_str: str,
    ) -> tuple[list[str], list[str]]:
        """获取股票池成分股。

        Returns:
            (price_symbols, financial_symbols)
            - price_symbols: 需下载行情的标的列表
            - financial_symbols: 需下载财务数据的标的列表（ETF 为空）
        """
        if universe == "all":
            stocks = self.client.get_sector_stocks(SECTOR_ALL_A)
            etfs = self.client.get_sector_stocks(SECTOR_ALL_ETF)
            if stocks:
                self.cache.save_universe(SECTOR_ALL_A, date_str, stocks)
            if etfs:
                self.cache.save_universe(SECTOR_ALL_ETF, date_str, etfs)
            price_symbols = sorted(set(stocks) | set(etfs))
            self._info(
                f"[股票池] 沪深A股 {len(stocks)} 只 + 沪深ETF {len(etfs)} 只 "
                f"= {len(price_symbols)} 只"
            )
            return price_symbols, stocks

        if universe == "all_a":
            stocks = self.client.get_sector_stocks(SECTOR_ALL_A)
            if stocks:
                self.cache.save_universe(SECTOR_ALL_A, date_str, stocks)
            self._info(f"[股票池] 沪深A股 {len(stocks)} 只")
            return stocks, stocks

        if universe == "etf":
            etfs = self.client.get_sector_stocks(SECTOR_ALL_ETF)
            if etfs:
                self.cache.save_universe(SECTOR_ALL_ETF, date_str, etfs)
            self._info(f"[股票池] 沪深ETF {len(etfs)} 只（无财务数据）")
            return etfs, []

        # 指数成分股（向后兼容：csi300/csi500/sse50/csi1000 等）
        provider = MiniQmtUniverseProvider(self.cache)
        symbols = provider.get_symbols(universe, date_str)
        self._info(f"[股票池] {universe}: {len(symbols)} 只")
        return symbols, symbols

    def download_prices(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        """分批下载行情数据并写入 DuckDB 缓存。"""
        total = len(symbols)
        if total == 0:
            self._warning("[行情] 无标的需要下载")
            return
        start_label = start_date or "(最早)"
        self._info(
            f"[行情] 开始下载 {total} 只标的行情 ({start_label} ~ {end_date})"
        )
        ok = 0
        total_batches = (total + batch_size - 1) // batch_size
        for i in range(0, total, batch_size):
            batch = symbols[i : i + batch_size]
            batch_num = i // batch_size + 1
            t0 = time.time()
            try:
                df = self.data_provider._fetch_kline(batch, start_date, end_date)
                if df is not None and not df.empty:
                    self.data_provider.cache.save_prices(df)
                    ok += len(batch)
                elapsed = time.time() - t0
                self._info(
                    f"[行情] 批次 {batch_num}/{total_batches} "
                    f"完成 ({len(batch)} 只, {elapsed:.1f}s)"
                )
            except Exception as e:
                self._warning(
                    f"[行情] 批次 {batch_num}/{total_batches} 失败: {e}"
                )
        self._info(f"[行情] 完成，{ok}/{total} 只标的成功")

    def download_financials(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        """分批下载财务数据并写入 DuckDB 缓存。"""
        total = len(symbols)
        if total == 0:
            self._info("[财务] 无标的需要下载（ETF 无财务数据）")
            return
        start_label = start_date or "(最早)"
        self._info(
            f"[财务] 开始下载 {total} 只股票财务数据 ({start_label} ~ {end_date})"
        )
        ok = 0
        total_batches = (total + batch_size - 1) // batch_size
        for i in range(0, total, batch_size):
            batch = symbols[i : i + batch_size]
            batch_num = i // batch_size + 1
            t0 = time.time()
            try:
                df = self.data_provider._fetch_financials(
                    batch, start_date, end_date
                )
                if df is not None and not df.empty:
                    self.data_provider.cache.save_financials(df)
                    ok += len(batch)
                elapsed = time.time() - t0
                self._info(
                    f"[财务] 批次 {batch_num}/{total_batches} "
                    f"完成 ({len(batch)} 只, {elapsed:.1f}s)"
                )
            except Exception as e:
                self._warning(
                    f"[财务] 批次 {batch_num}/{total_batches} 失败: {e}"
                )
        self._info(f"[财务] 完成，{ok}/{total} 只股票成功")

    def run(
        self,
        universe: str = "all",
        start_date: str = "",
        end_date: str = "",
        skip_financial: bool = False,
        batch_size: int = BATCH_SIZE,
    ) -> dict[str, Any]:
        """执行完整下载流程。

        Args:
            universe: 股票池类型（all/all_a/etf/csi300/csi500/sse50/csi1000）
            start_date: 起始日期 YYYY-MM-DD，空字符串=最长历史
            end_date: 结束日期 YYYY-MM-DD，空字符串=今天
            skip_financial: 跳过财务数据下载
            batch_size: 分批下载每批数量

        Returns:
            执行结果摘要 dict
        """
        end = end_date or date.today().strftime("%Y-%m-%d")

        self._info("=" * 60)
        self._info("全量数据下载")
        self._info(f"股票池: {universe}")
        self._info(f"日期范围: {start_date or '(最早)'} ~ {end}")
        self._info(f"批次大小: {batch_size}")
        self._info("=" * 60)

        if not self.is_available:
            self._warning(
                "xtquant 不可用，无法下载数据。请确保 miniQMT 客户端已连接。"
            )
            return {"status": "error", "reason": "xtquant_unavailable"}

        date_str = end.replace("-", "")

        price_symbols, financial_symbols = self.get_universe_symbols(
            universe, date_str
        )
        if not price_symbols:
            self._warning("股票池为空，终止")
            return {"status": "error", "reason": "empty_universe"}

        self.download_prices(price_symbols, start_date, end, batch_size)

        if skip_financial:
            self._info("[财务] 已跳过（skip_financial=True）")
        else:
            self.download_financials(
                financial_symbols, start_date, end, batch_size
            )

        self._info("=" * 60)
        self._info(f"数据下载完成！缓存路径: {self.cache.db_path}")
        self._info("=" * 60)

        with contextlib.suppress(Exception):
            self.cache.close()

        return {
            "status": "ok",
            "universe": universe,
            "price_symbols": len(price_symbols),
            "financial_symbols": len(financial_symbols),
            "cache_path": str(self.cache.db_path),
        }

    # ── 内部工具 ──────────────────────────────────────────────────

    def _info(self, msg: str) -> None:
        if self.logger:
            self.logger.info(msg)

    def _warning(self, msg: str) -> None:
        if self.logger:
            self.logger.warning(msg)
