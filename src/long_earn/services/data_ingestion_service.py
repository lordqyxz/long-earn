"""数据下载服务 — 行情与财务数据批量入库。

从 scripts/download_data.py 抽取的核心业务逻辑，供 CLI / Web 等入口复用。
依赖 MiniQMT 客户端（xtquant）与 DuckDB 缓存。
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

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

# 并发下载子进程数：miniQMT 本地服务，4 并发平衡吞吐与稳定性
# 太高（>8）易触发 xtquant C++ 端 SIGABRT；subprocess 隔离使单批崩溃不影响整体
DEFAULT_MAX_WORKERS = 4

# 子进程下载超时（秒）
_SUBPROCESS_TIMEOUT = 180

# 项目根目录（供子进程 cwd 使用，确保 src layout 可 import）
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

# 子进程 worker 脚本：在独立进程中调用 xtquant 下载并输出 DataFrame 到 pickle 文件
# 用 __new__ 绕过 MiniQmtDataProvider.__init__（避免子进程创建 DuckDB 连接引发并发写锁）
_SUBPROCESS_WORKER = """
import os, json, sys
from long_earn.backtest.data.miniqmt_provider import MiniQmtDataProvider, MiniQmtClient

def main():
    batch = json.loads(os.environ["BATCH_JSON"])
    start = os.environ["START_DATE"]
    end = os.environ["END_DATE"]
    kind = os.environ["KIND"]
    out_path = os.environ["OUT_PATH"]

    p = MiniQmtDataProvider.__new__(MiniQmtDataProvider)
    p.client = MiniQmtClient.get()

    if kind == "price":
        df = p._fetch_kline(batch, start, end)
    else:
        df = p._fetch_financials(batch, start, end)

    if df is not None and not df.empty:
        df.to_pickle(out_path)
        print(f"OK {len(df)}")
    else:
        print("EMPTY")

main()
"""


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
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> None:
        """分批并发下载行情数据并写入 DuckDB 缓存。

        使用 subprocess 隔离每个批次，防止单只股票触发 xtquant C++ SIGABRT
        导致整个进程崩溃。并发数由 max_workers 控制。
        """
        self._download_concurrent(
            symbols, start_date, end_date, "price", batch_size, max_workers
        )

    def download_financials(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        batch_size: int = BATCH_SIZE,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> None:
        """分批并发下载财务数据并写入 DuckDB 缓存。"""
        if not symbols:
            self._info("[财务] 无标的需要下载（ETF 无财务数据）")
            return
        self._download_concurrent(
            symbols, start_date, end_date, "financial", batch_size, max_workers
        )

    def _download_concurrent(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        kind: str,
        batch_size: int,
        max_workers: int,
    ) -> None:
        """并发下载通用实现（行情/财务共用）。

        两阶段：
        1. 并发 subprocess 下载 + 读取，输出 DataFrame 到 pickle 临时文件
           （subprocess 隔离防 SIGABRT；ThreadPoolExecutor 管理并发）
        2. 串行读取 pickle 写入 DuckDB（避免多进程并发写锁冲突）
        """
        total = len(symbols)
        if total == 0:
            self._warning(f"[{kind}] 无标的需要下载")
            return

        tag = "行情" if kind == "price" else "财务"
        start_label = start_date or "(最早)"
        self._info(
            f"[{tag}] 开始下载 {total} 只标的 ({start_label} ~ {end_date}), "
            f"并发={max_workers}"
        )

        batches = [symbols[i : i + batch_size] for i in range(0, total, batch_size)]
        total_batches = len(batches)

        # 阶段1：并发下载到子进程本地 pickle
        results: list[tuple[bool, str] | None] = [None] * total_batches
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_meta: dict[Any, tuple[int, str]] = {}
            for idx, batch in enumerate(batches):
                tmp = tempfile.mktemp(suffix=".pkl")
                fut = pool.submit(
                    self._run_batch_subprocess,
                    batch, start_date, end_date, kind, tmp,
                )
                future_to_meta[fut] = (idx, tmp)

            done = 0
            for fut in as_completed(future_to_meta):
                idx, tmp = future_to_meta[fut]
                ok = fut.result()
                results[idx] = (ok, tmp)
                done += 1
                if done % 5 == 0 or done == total_batches:
                    self._info(f"[{tag}] 下载进度 {done}/{total_batches}")

        # 阶段2：串行写入 DuckDB
        ok_count = 0
        failed_count = 0
        for idx, item in enumerate(results):
            batch_num = idx + 1
            batch = batches[idx]
            ok, tmp = item  # type: ignore[misc]
            if ok and os.path.exists(tmp):
                try:
                    df = pd.read_pickle(tmp)
                    if not df.empty:
                        if kind == "price":
                            self.data_provider.cache.save_prices(df)
                        else:
                            self.data_provider.cache.save_financials(df)
                        ok_count += len(batch)
                except Exception as e:
                    self._warning(
                        f"[{tag}] 批次 {batch_num}/{total_batches} 写入失败: {e}"
                    )
                    failed_count += len(batch)
            else:
                failed_count += len(batch)
            with contextlib.suppress(FileNotFoundError, OSError):
                os.unlink(tmp)
            if batch_num % 5 == 0 or batch_num == total_batches:
                self._info(
                    f"[{tag}] 写入进度 {batch_num}/{total_batches} "
                    f"({ok_count} 成功, {failed_count} 失败)"
                )

        msg = f"[{tag}] 完成，{ok_count}/{total} 只成功"
        if failed_count:
            msg += f"，{failed_count} 只失败"
        self._info(msg)

    def _run_batch_subprocess(
        self,
        batch: list[str],
        start_date: str,
        end_date: str,
        kind: str,
        out_path: str,
        timeout: int = _SUBPROCESS_TIMEOUT,
    ) -> bool:
        """在子进程中下载一个批次，DataFrame 输出到 pickle 文件。

        子进程隔离防 SIGABRT：xtquant C++ 端对特定股票触发 abort 时只杀子进程，
        主进程存活并记录失败批次。
        """
        env = {
            **os.environ,
            "BATCH_JSON": json.dumps(batch),
            "START_DATE": start_date,
            "END_DATE": end_date,
            "KIND": kind,
            "OUT_PATH": out_path,
            "LONG_EARN_DISABLE_XTQUANT": "",  # 子进程清除禁用开关
        }
        try:
            r = subprocess.run(
                [sys.executable, "-c", _SUBPROCESS_WORKER],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                cwd=str(_PROJECT_ROOT),
            )
            if r.returncode != 0:
                self._warning(
                    f"[下载] 子进程崩溃 exitcode={r.returncode} "
                    f"({len(batch)} 只, kind={kind})"
                )
                return False
            # stdout 可能含 xtquant banner，OK/EMPTY 在最后一行
            last_line = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
            return last_line.startswith("OK")
        except subprocess.TimeoutExpired:
            self._warning(
                f"[下载] 子进程超时 ({timeout}s, {len(batch)} 只, kind={kind})"
            )
            return False

    def run(
        self,
        universe: str = "all",
        start_date: str = "",
        end_date: str = "",
        skip_financial: bool = False,
        batch_size: int = BATCH_SIZE,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> dict[str, Any]:
        """执行完整下载流程。

        Args:
            universe: 股票池类型（all/all_a/etf/csi300/csi500/sse50/csi1000）
            start_date: 起始日期 YYYY-MM-DD，空字符串=最长历史
            end_date: 结束日期 YYYY-MM-DD，空字符串=今天
            skip_financial: 跳过财务数据下载
            batch_size: 分批下载每批数量
            max_workers: 并发下载子进程数（1-8，默认 4）

        Returns:
            执行结果摘要 dict
        """
        max_workers = max(1, min(8, max_workers))
        end = end_date or date.today().strftime("%Y-%m-%d")

        self._info("=" * 60)
        self._info("全量数据下载")
        self._info(f"股票池: {universe}")
        self._info(f"日期范围: {start_date or '(最早)'} ~ {end}")
        self._info(f"批次大小: {batch_size}")
        self._info(f"并发数: {max_workers}")
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

        self.download_prices(price_symbols, start_date, end, batch_size, max_workers)

        if skip_financial:
            self._info("[财务] 已跳过（skip_financial=True）")
        else:
            self.download_financials(
                financial_symbols, start_date, end, batch_size, max_workers
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
