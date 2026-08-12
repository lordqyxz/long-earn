"""miniQMT 数据采集执行器 — 行情与财务数据增量入库。

由 ``IncrementalSyncService`` 协调调用，负责 miniQMT 采集和 DuckDB 写入。
依赖 MiniQMT 客户端（xtquant）与 DuckDB 本地数据层。
"""

from __future__ import annotations

import contextlib
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import pandas as pd

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.miniqmt_provider import (
    BOARD_NAME_MAP,
    INDEX_SECTOR_MAP,
    MiniQmtClient,
    MiniQmtDataProvider,
    MiniQmtUniverseProvider,
)

if TYPE_CHECKING:
    from long_earn.services import LoggerService

# 分批下载默认大小（CLI --batch-size 兜底值）
# 实测 xtquant 接口对 batch size 线性扩展，无大 batch 惩罚（见 _measure_batch_scaling）
# 行情/财务分别有更优的 per-kind 默认值（下方 _PRICE_BATCH / _FINANCIAL_BATCH）
BATCH_SIZE = 50

# 行情/财务最优 batch size（实测确定，xtquant 接口线性扩展，约束是单批不超 60s 超时）
# 行情：200 只/批 ≈ 8s（2.5 个月数据），全历史约 30-40s，留余量
# 财务：100 只/批 ≈ 17s（全历史四表合并），离 60s 超时有余量
_PRICE_BATCH = 200
_FINANCIAL_BATCH = 100

# 全量下载的板块名
SECTOR_ALL_A = "沪深A股"
SECTOR_ALL_ETF = "沪深ETF"

# 保留用于签名兼容（主进程直接调 xtquant，单线程串行，max_workers 实际忽略）
DEFAULT_MAX_WORKERS = 4

# 财务数据新鲜度阈值：最新公告日距今超此天数视为需要增量补齐
# 基于 announce_date（公告日，PIT 真实可见日），而非 report_date（报告期末）
_FINANCIAL_STALE_DAYS = 120


class DataIngestionService:
    """miniQMT 采集与 DuckDB 写入执行器。

    封装行情/财务数据的批量下载与入库逻辑，与 CLI 表现层解耦。
    """

    def __init__(self, logger: LoggerService | None = None) -> None:
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
        batch_size: int = _PRICE_BATCH,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> None:
        """分批串行下载行情数据并写入 DuckDB 缓存。

        主进程直接调 xtquant，单线程串行（无 subprocess 隔离）。
        若 xtquant SIGABRT 杀死主进程，靠外层守护脚本重启 + 缓存检测断点续传。
        """
        self._download_concurrent(
            symbols, start_date, end_date, "price", batch_size, max_workers
        )

    def _select_prices_to_refresh(
        self,
        symbols: list[str],
        end_date: str,
        start_date: str = "",
    ) -> tuple[list[str], list[str], str]:
        """行情增量预检：按交易日精确判定缺失，分两组返回需下载的股票。

        判定规则（按缓存最新交易日精确比对，缺一天就补一天）：
        - 缓存为空（无该 symbol 记录）→ full_missing 组，起始日沿用 start_date（空=最早）
        - 最新交易日 < end_date → stale 组，起始日 = 最新交易日 + 1 天
        - 最新交易日 >= end_date → 跳过（已齐到目标日）

        分组返回的原因：无缓存股票需要全量历史（起始日可能为空=最早），
        待补股票只需几天数据；若混用统一起始日会导致待补股票也全量重下，触发超时。

        Args:
            symbols: 候选股票列表
            end_date: 目标截止日 YYYY-MM-DD（通常为今天）
            start_date: 全量缺失股票的回溯起始日（空字符串=最早历史）

        Returns:
            (full_missing, stale, stale_start)
            - full_missing: 无缓存的股票列表，需用 start_date 全量下载
            - stale: 有缓存但缺交易日的股票列表，需从 stale_start 起补齐
            - stale_start: stale 组的下载起始日（取最早待补起始日）；
              stale 为空时返回 end_date（无意义，调用方应检查 stale 是否为空）
        """
        end_ts = pd.Timestamp(end_date)
        latest_map = self.cache.get_price_latest_dates(symbols)
        full_missing: list[str] = []
        stale: list[str] = []
        stale_starts: list[str] = []
        fresh_count = 0

        for sym in symbols:
            latest = latest_map.get(sym)
            if latest is None:
                # 缓存为空：全量下载
                full_missing.append(sym)
                continue

            latest_ts = pd.Timestamp(latest)
            if latest_ts < end_ts:
                # 缺交易日：从最新交易日次日起补齐到 end_date
                inc_start = (latest_ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                stale.append(sym)
                stale_starts.append(inc_start)
            else:
                fresh_count += 1

        stale_start = min(stale_starts) if stale_starts else end_date

        self._info(
            f"[行情][增量] 共 {len(symbols)} 只，需刷新 "
            f"{len(full_missing) + len(stale)} 只"
            f"（无缓存 {len(full_missing)} / 待补 {len(stale)}），跳过 {fresh_count} 只"
        )
        return full_missing, stale, stale_start

    def download_prices_incremental(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        batch_size: int = _PRICE_BATCH,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> None:
        """行情增量下载：按交易日精确判定，缺多少天就补多少天。

        分两阶段下载（避免无缓存股票的全量历史拖累待补股票）：
        1. stale 组：有缓存但缺交易日的股票，从各自最新交易日次日起补齐到 end_date
        2. full_missing 组：无缓存的股票，用 start_date 全量下载
        靠 INSERT OR REPLACE upsert 幂等合并到 DuckDB 缓存。
        """
        if not symbols:
            self._info("[行情] 无标的需要下载")
            return
        full_missing, stale, stale_start = self._select_prices_to_refresh(
            symbols, end_date, start_date
        )
        if not full_missing and not stale:
            self._info(
                f"[行情][增量] 全部 {len(symbols)} 只行情已齐到 {end_date}，跳过下载"
            )
            return

        # 阶段1：待补股票（只缺几天，快速）
        if stale:
            self._info(
                f"[行情][增量] 阶段1：{len(stale)} 只待补，"
                f"起始日 {stale_start} ~ {end_date}"
            )
            self._download_concurrent(
                stale, stale_start, end_date, "price", batch_size, max_workers
            )

        # 阶段2：无缓存股票（全量历史，耗时）
        if full_missing:
            self._info(
                f"[行情][增量] 阶段2：{len(full_missing)} 只无缓存，"
                f"起始日 {start_date or '(最早)'} ~ {end_date}"
            )
            self._download_concurrent(
                full_missing, start_date, end_date, "price", batch_size, max_workers
            )

    def download_financials(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        batch_size: int = _FINANCIAL_BATCH,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> None:
        """分批并发下载财务数据并写入 DuckDB 缓存。"""
        if not symbols:
            self._info("[财务] 无标的需要下载（ETF 无财务数据）")
            return
        self._download_concurrent(
            symbols, start_date, end_date, "financial", batch_size, max_workers
        )

    def _select_financials_to_refresh(
        self,
        symbols: list[str],
        today: str,
        start_date: str = "",
    ) -> tuple[list[str], list[str], str]:
        """财务增量预检：按公告日阈值判定，分两组返回需下载的股票。

        判定规则（按 announce_date 最新公告日阈值）：
        - 缓存为空（无该 symbol 记录）→ full_missing 组，起始日沿用 start_date（空=最早）
        - 最新公告日距今 > _FINANCIAL_STALE_DAYS → stale 组，起始日 = 最新公告日 + 1 天
        - 最新公告日距今 ≤ 阈值 → 跳过（最近数据已齐）

        分组返回的原因：无缓存股票需要全量历史，过期股票只需补最近；
        若混用统一起始日会导致过期股票也全量重下，浪费带宽。

        Args:
            symbols: 候选股票列表
            today: 今日日期 YYYY-MM-DD
            start_date: 全量缺失股票的回溯起始日（空字符串=最早历史）

        Returns:
            (full_missing, stale, stale_start)
            - full_missing: 无缓存的股票列表，需用 start_date 全量下载
            - stale: 有缓存但公告日过期的股票列表，需从 stale_start 起补齐
            - stale_start: stale 组的下载起始日（取最早过期起始日）；
              stale 为空时返回 today（无意义，调用方应检查 stale 是否为空）
        """
        today_ts = pd.Timestamp(today)
        threshold = timedelta(days=_FINANCIAL_STALE_DAYS)
        # 批量查最新公告日，避免逐股查 5208 次
        latest_map = self.cache.get_financial_latest_announces(symbols)
        full_missing: list[str] = []
        stale: list[str] = []
        stale_starts: list[str] = []
        fresh_count = 0

        for sym in symbols:
            latest_announce = latest_map.get(sym)
            if latest_announce is None:
                # 缓存为空：全量下载
                full_missing.append(sym)
                continue

            latest_ts = pd.Timestamp(latest_announce)
            if (today_ts - latest_ts) > threshold:
                # 过期：从最新公告日次日起补齐
                inc_start = (latest_ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                stale.append(sym)
                stale_starts.append(inc_start)
            else:
                fresh_count += 1

        stale_start = min(stale_starts) if stale_starts else today

        self._info(
            f"[财务][增量] 共 {len(symbols)} 只，需刷新 "
            f"{len(full_missing) + len(stale)} 只"
            f"（无缓存 {len(full_missing)} / 过期 {len(stale)}），跳过 {fresh_count} 只"
        )
        return full_missing, stale, stale_start

    def download_financials_incremental(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        batch_size: int = _FINANCIAL_BATCH,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> None:
        """财务增量下载：按公告日阈值判定，仅下载缺失/过期的股票。

        分两阶段下载（避免无缓存股票的全量历史拖累过期股票）：
        1. stale 组：公告日过期的股票，从各自最新公告日次日起补齐到 end_date
        2. full_missing 组：无缓存的股票，用 start_date 全量下载
        靠 INSERT OR REPLACE upsert 幂等合并到 DuckDB 缓存。
        """
        if not symbols:
            self._info("[财务] 无标的需要下载（ETF 无财务数据）")
            return
        today = end_date or date.today().strftime("%Y-%m-%d")
        full_missing, stale, stale_start = self._select_financials_to_refresh(
            symbols, today, start_date
        )
        if not full_missing and not stale:
            self._info(f"[财务][增量] 全部 {len(symbols)} 只最近数据已齐，跳过下载")
            return

        # 阶段1：过期股票（只补最近，快速）
        if stale:
            self._info(
                f"[财务][增量] 阶段1：{len(stale)} 只过期，"
                f"起始日 {stale_start} ~ {today}"
            )
            self._download_concurrent(
                stale, stale_start, today, "financial", batch_size, max_workers
            )

        # 阶段2：无缓存股票（全量历史，耗时）
        if full_missing:
            self._info(
                f"[财务][增量] 阶段2：{len(full_missing)} 只无缓存，"
                f"起始日 {start_date or '(最早)'} ~ {today}"
            )
            self._download_concurrent(
                full_missing, start_date, today, "financial", batch_size, max_workers
            )

    def _download_concurrent(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        kind: str,
        batch_size: int,
        max_workers: int,  # 保留参数兼容签名，单线程直接调用忽略
    ) -> None:
        """单线程串行下载+写入：主进程直接调 xtquant 下载一批 → 写入一批 → 下一批。

        xtquant 下载和 DuckDB 写入均单线程串行执行，避免并发触发 xtquant
        C++ SIGABRT，也避免 DuckDB 单连接的线程安全问题。
        若 xtquant SIGABRT 杀死主进程，外层守护脚本会重启进程，
        靠智能模式的缓存检测（get_price_latest_dates / get_financial_latest_announces）
        跳过已写入的股票，从断点续传。

        ADR-014 阶段 B：financial 分支改为按 8 张表分别取数（_fetch_financials_by_table），
        覆盖 Capital/Holdernum/Top10holder/Top10flowholder（旧路径只下 4 表）。
        """
        total = len(symbols)
        if total == 0:
            self._warning(f"[{kind}] 无标的需要下载")
            return

        tag = "行情" if kind == "price" else "财务"
        start_label = start_date or "(最早)"
        self._info(
            f"[{tag}] 开始下载 {total} 只标的 ({start_label} ~ {end_date}), 单线程"
        )

        batches = [symbols[i : i + batch_size] for i in range(0, total, batch_size)]
        total_batches = len(batches)

        ok_count = 0
        failed_count = 0
        for idx, batch in enumerate(batches):
            batch_num = idx + 1
            if kind == "price":
                # 行情：旧路径（单 DataFrame）
                df = self._fetch_batch(batch, start_date, end_date, kind)
                wrote_ok = self._write_batch_to_cache(
                    kind, df, batch_num, total_batches
                )
            else:
                # ADR-014 阶段 B：财务按 8 表分别取数（_fetch_financials_by_table
                # 内部已 save_financial_table 写各自细表，覆盖全部 8 张表）
                wrote_ok = self._fetch_financial_batch_by_table(
                    batch, start_date, end_date, batch_num, total_batches
                )
            if wrote_ok:
                ok_count += len(batch)
            else:
                failed_count += len(batch)
            if batch_num % 5 == 0 or batch_num == total_batches:
                self._info(
                    f"[{tag}] 进度 {batch_num}/{total_batches} "
                    f"({ok_count} 成功, {failed_count} 失败)"
                )

        msg = f"[{tag}] 完成，{ok_count}/{total} 只成功"
        if failed_count:
            msg += f"，{failed_count} 只失败"
        self._info(msg)

    def _fetch_financial_batch_by_table(
        self,
        batch: list[str],
        start_date: str,
        end_date: str,
        batch_num: int,
        total_batches: int,
    ) -> bool:
        """ADR-014 阶段 B：按 8 张表分别取数并写入各自细表。

        ``_fetch_financials_by_table`` 内部已调 ``cache.save_financial_table``
        写入对应细表，本方法只负责异常隔离 + 进度日志。
        覆盖 Income/Balance/CashFlow/Pershareindex/Capital/Holdernum/
        Top10holder/Top10flowholder 全部 8 张表。
        """
        try:
            table_dfs = self.data_provider._fetch_financials_by_table(
                batch,
                start_date,
                end_date,
            )
            if not table_dfs:
                return False
            if batch_num % 5 == 0 or batch_num == total_batches:
                tables_str = ", ".join(
                    f"{name}:{len(df)}" for name, df in table_dfs.items()
                )
                self._info(
                    f"[财务] 写入进度 {batch_num}/{total_batches} ({tables_str})"
                )
            return True
        except Exception as e:
            self._warning(f"[财务] 批次 {batch_num}/{total_batches} 下载失败: {e}")
            return False

    def _fetch_batch(
        self,
        batch: list[str],
        start_date: str,
        end_date: str,
        kind: str,
    ) -> pd.DataFrame | None:
        """主进程直接调 xtquant 下载一批数据。

        若 xtquant C++ 端触发 SIGABRT，主进程会被杀（无 subprocess 隔离），
        靠外层守护脚本重启 + 智能模式缓存检测实现断点续传。
        """
        try:
            if kind == "price":
                return self.data_provider._fetch_kline(batch, start_date, end_date)
            return self.data_provider._fetch_financials(batch, start_date, end_date)
        except Exception as e:
            self._warning(f"[下载] 批次下载异常: {e}")
            return None

    def _write_batch_to_cache(
        self,
        kind: str,
        df: pd.DataFrame | None,
        batch_num: int,
        total_batches: int,
    ) -> bool:
        """将一批 DataFrame 写入 DuckDB 缓存（单线程串行调用）。

        Returns:
            True 表示本批写入成功，False 表示失败/空
        """
        tag = "行情" if kind == "price" else "财务"
        if df is None or df.empty:
            return False
        try:
            if kind == "price":
                self.data_provider.cache.save_prices(df)
            else:
                self.data_provider.cache.save_financials(df)
            if batch_num % 5 == 0 or batch_num == total_batches:
                self._info(f"[{tag}] 写入进度 {batch_num}/{total_batches}")
            return True
        except Exception as e:
            self._warning(f"[{tag}] 批次 {batch_num}/{total_batches} 写入失败: {e}")
            return False

    def run(
        self,
        universe: str = "all",
        start_date: str = "",
        end_date: str = "",
        skip_financial: bool = False,
        batch_size: int = 0,
        max_workers: int = DEFAULT_MAX_WORKERS,
        full: bool = False,
    ) -> dict[str, Any]:
        """执行数据下载流程（默认智能模式：自动分析缺失，只下载缺失/过期的部分）。

        智能模式（默认，full=False）：
        - 行情：按交易日精确判定，缓存最新交易日 < end_date 的股票从次日起补齐；
          缓存为空的股票全量下载；已齐到 end_date 的跳过
        - 财务：按公告日阈值判定，最新公告日距今 > 120 天的股票补齐；
          缓存为空的股票全量下载；新鲜的跳过
        强制全量模式（full=True）：忽略缓存，全部股票按 [start_date, end_date] 全量重下

        Args:
            batch_size: 分批大小；0=自动（行情200/财务100，实测最优），
                显式指定则覆盖自动值

        Returns:
            执行结果摘要 dict
        """
        max_workers = max(1, min(8, max_workers))
        end = end_date or date.today().strftime("%Y-%m-%d")
        # 0=自动：行情用 _PRICE_BATCH，财务用 _FINANCIAL_BATCH
        price_batch = batch_size or _PRICE_BATCH
        financial_batch = batch_size or _FINANCIAL_BATCH

        self._info("=" * 60)
        self._info("数据下载" + ("（强制全量）" if full else "（智能增量）"))
        self._info(f"股票池: {universe}")
        self._info(f"日期范围: {start_date or '(最早)'} ~ {end}")
        self._info(f"批次大小: 行情={price_batch} / 财务={financial_batch}")
        self._info("=" * 60)

        if not self.is_available:
            self._warning("xtquant 不可用，无法下载数据。请确保 miniQMT 客户端已连接。")
            return {"status": "error", "reason": "xtquant_unavailable"}

        date_str = end.replace("-", "")

        price_symbols, financial_symbols = self.get_universe_symbols(universe, date_str)
        if not price_symbols:
            self._warning("股票池为空，终止")
            return {"status": "error", "reason": "empty_universe"}

        # 行情：全量 or 智能增量
        if full:
            self.download_prices(
                price_symbols, start_date, end, price_batch, max_workers
            )
        else:
            self.download_prices_incremental(
                price_symbols, start_date, end, price_batch, max_workers
            )

        # 财务：全量 or 智能增量 or 跳过
        if skip_financial:
            self._info("[财务] 已跳过（skip_financial=True）")
        elif full:
            self.download_financials(
                financial_symbols, start_date, end, financial_batch, max_workers
            )
        else:
            self.download_financials_incremental(
                financial_symbols, start_date, end, financial_batch, max_workers
            )

        # P1-01：采集成分股快照，积累历史 PIT 数据
        self._collect_universe_snapshots()

        # 采集标的详情（公司名称、行业等），写入 instrument_details 表
        self._download_instrument_details(price_symbols)

        self._info("=" * 60)
        self._info(f"数据下载完成！缓存路径: {self.cache.db_path}")
        self._info("=" * 60)

        with contextlib.suppress(Exception):
            self.cache.close()

        return {
            "status": "ok",
            "universe": universe,
            "mode": "full" if full else "smart",
            "price_symbols": len(price_symbols),
            "financial_symbols": len(financial_symbols),
            "cache_path": str(self.cache.db_path),
        }

    # ── P1-01 成分股快照采集 ──────────────────────────────────────

    def _collect_universe_snapshots(self) -> None:
        """采集当前成分股快照，积累历史 PIT 数据。

        每次下载数据时采集一次当前成分股，随着时间推移积累多日期快照，
        逐步消除幸存者偏差。只采集 miniqmt 直接支持的指数/板块。
        """
        if not self.is_available:
            self._info("[成分股快照] xtquant 不可用，跳过")
            return

        # 待采集的指数/板块清单
        targets: dict[str, str] = {}

        # 四大指数
        targets.update(INDEX_SECTOR_MAP)

        # 板块（沪深主板、创业板、科创板等）
        for board_key, board_name in BOARD_NAME_MAP.items():
            if board_name not in ("all_a", "全A股", "沪深A股"):
                targets[board_key] = board_name

        self._info(f"[成分股快照] 开始采集 {len(targets)} 个指数/板块的当前成分股...")
        collected = 0
        for key, name in targets.items():
            try:
                symbols = self.data_provider.get_sector_stocks(name)
                if symbols:
                    self.cache.save_universe(key, "", sorted(symbols))
                    collected += 1
            except Exception as exc:
                self._warning(f"[成分股快照] {name} 采集失败: {exc}")

        self._info(f"[成分股快照] 完成：{collected}/{len(targets)} 个指数/板块")

    # ── 标的详情采集 ──────────────────────────────────────────────

    def _download_instrument_details(self, symbols: list[str]) -> None:
        """批量下载标的详情（公司名称、行业、上市日期等）并写入缓存。

        从 xtquant get_instrument_detail 逐个获取，分批写入 DuckDB。
        下载完成后通过 THY1/DY1 板块 API 批量回填 industry + region
        （get_instrument_detail 不含行业/地区字段）。
        xtquant 不可用时跳过（已有缓存数据仍可使用）。
        """
        if not self.is_available:
            self._info("[标的详情] xtquant 不可用，跳过")
            return

        self._info(f"[标的详情] 开始下载 {len(symbols)} 只标的详情...")

        # 先检查缓存中已有的标的，避免重复下载
        existing_names = self.cache.get_instrument_names_batch(symbols)
        missing = [s for s in symbols if s not in existing_names]
        if not missing:
            self._info(f"[标的详情] 全部 {len(symbols)} 只标的已缓存，跳过")
        else:
            self._info(
                f"[标的详情] 需下载 {len(missing)}/{len(symbols)} 只"
                f"（已缓存 {len(existing_names)}）"
            )

            batch: list[tuple[str, dict[str, Any]]] = []
            batch_size = 50
            collected = 0
            for i, sym in enumerate(missing, 1):
                try:
                    detail = self.client.get_instrument_detail(sym)
                    if detail:
                        batch.append((sym, detail))
                        collected += 1
                    if len(batch) >= batch_size or i == len(missing):
                        self.cache.save_instrument_details_batch(batch)
                        batch.clear()
                    if i % 200 == 0 or i == len(missing):
                        self._info(f"[标的详情] 进度 {i}/{len(missing)}")
                except Exception as exc:
                    self._warning(f"[标的详情] {sym} 获取失败: {exc}")

            self._info(f"[标的详情] 完成：{collected}/{len(missing)} 条新增")

        # 通过 THY1/DY1 板块批量回填 industry + region
        self._enrich_sectors_from_xtquant()

    def _enrich_sectors_from_xtquant(self) -> None:
        """通过 xtquant THY1/DY1 板块批量回填 industry + region 到缓存。

        get_instrument_detail 不含行业/地区字段，需通过板块成分股反查。
        仅更新空值行，不覆盖已有数据。
        """
        self._info("[板块回填] 通过 THY1/DY1 板块批量补充行业+地区...")

        # 行业
        industry_map = self.client.build_sector_mapping("THY1")
        if industry_map:
            self.cache.batch_update_instrument_sectors(industry_map, "industry")
            self._info(f"[板块回填] 行业映射 {len(industry_map)} 只股票")

        # 地域
        region_map = self.client.build_sector_mapping("DY1")
        if region_map:
            self.cache.batch_update_instrument_sectors(region_map, "region")
            self._info(f"[板块回填] 地区映射 {len(region_map)} 只股票")

        self._info("[板块回填] 完成")

    # ── 内部工具 ──────────────────────────────────────────────────

    def _info(self, msg: str) -> None:
        if self.logger:
            self.logger.info(msg)

    def _warning(self, msg: str) -> None:
        if self.logger:
            self.logger.warning(msg)
