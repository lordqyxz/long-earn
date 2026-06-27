#!/usr/bin/env python3
"""下载全量行情与财务数据到 DuckDB 缓存。

支持全量下载沪深A股 + 沪深ETF 的最长历史行情数据，以及 A 股财务数据。
数据写入 ~/.long_earn/backtest_cache.duckdb，供回测引擎离线使用。

用法:
    # 全量下载（A股行情+财务 + ETF行情），最长历史至最新
    uv run python scripts/download_data.py

    # 仅 A 股（行情 + 财务）
    uv run python scripts/download_data.py --universe all_a

    # 仅 ETF（行情，无财务数据）
    uv run python scripts/download_data.py --universe etf

    # 指定日期范围
    uv run python scripts/download_data.py --start 2010-01-01 --end 2026-06-28

    # 跳过财务数据
    uv run python scripts/download_data.py --skip-financial

    # 指数成分股（向后兼容）
    uv run python scripts/download_data.py --universe csi300+csi500
"""

from __future__ import annotations

import argparse
import contextlib
import sys
import time
from datetime import date
from pathlib import Path

from loguru import logger

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from long_earn.backtest.data.cache import DataCache  # noqa: E402
from long_earn.backtest.data.miniqmt_provider import (  # noqa: E402
    MiniQmtClient,
    MiniQmtDataProvider,
    MiniQmtUniverseProvider,
)

logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    ),
)

# 分批下载，避免 xtquant 单次请求过大超时
BATCH_SIZE = 50

# 全量下载的板块名
SECTOR_ALL_A = "沪深A股"
SECTOR_ALL_ETF = "沪深ETF"


def get_universe_symbols(
    client: MiniQmtClient,
    universe: str,
    date_str: str,
    cache: DataCache,
) -> tuple[list[str], list[str]]:
    """获取股票池成分股。

    Returns:
        (price_symbols, financial_symbols)
        - price_symbols: 需下载行情的标的列表
        - financial_symbols: 需下载财务数据的标的列表（ETF 为空）
    """
    if universe == "all":
        stocks = client.get_sector_stocks(SECTOR_ALL_A)
        etfs = client.get_sector_stocks(SECTOR_ALL_ETF)
        if stocks:
            cache.save_universe(SECTOR_ALL_A, date_str, stocks)
        if etfs:
            cache.save_universe(SECTOR_ALL_ETF, date_str, etfs)
        price_symbols = sorted(set(stocks) | set(etfs))
        logger.info(
            f"[股票池] 沪深A股 {len(stocks)} 只 + 沪深ETF {len(etfs)} 只 "
            f"= {len(price_symbols)} 只"
        )
        return price_symbols, stocks

    if universe == "all_a":
        stocks = client.get_sector_stocks(SECTOR_ALL_A)
        if stocks:
            cache.save_universe(SECTOR_ALL_A, date_str, stocks)
        logger.info(f"[股票池] 沪深A股 {len(stocks)} 只")
        return stocks, stocks

    if universe == "etf":
        etfs = client.get_sector_stocks(SECTOR_ALL_ETF)
        if etfs:
            cache.save_universe(SECTOR_ALL_ETF, date_str, etfs)
        logger.info(f"[股票池] 沪深ETF {len(etfs)} 只（无财务数据）")
        return etfs, []

    # 指数成分股（向后兼容：csi300/csi500/sse50/csi1000 等）
    provider = MiniQmtUniverseProvider(cache)
    symbols = provider.get_symbols(universe, date_str)
    logger.info(f"[股票池] {universe}: {len(symbols)} 只")
    return symbols, symbols


def download_prices(
    data_provider: MiniQmtDataProvider,
    symbols: list[str],
    start_date: str,
    end_date: str,
    batch_size: int,
) -> None:
    """分批下载行情数据并写入 DuckDB 缓存。"""
    total = len(symbols)
    if total == 0:
        logger.warning("[行情] 无标的需要下载")
        return
    start_label = start_date or "(最早)"
    logger.info(f"[行情] 开始下载 {total} 只标的行情 ({start_label} ~ {end_date})")
    ok = 0
    total_batches = (total + batch_size - 1) // batch_size
    for i in range(0, total, batch_size):
        batch = symbols[i : i + batch_size]
        batch_num = i // batch_size + 1
        t0 = time.time()
        try:
            df = data_provider._fetch_kline(batch, start_date, end_date)
            if df is not None and not df.empty:
                data_provider.cache.save_prices(df)
                ok += len(batch)
            elapsed = time.time() - t0
            logger.info(
                f"[行情] 批次 {batch_num}/{total_batches} "
                f"完成 ({len(batch)} 只, {elapsed:.1f}s)"
            )
        except Exception as e:
            logger.warning(f"[行情] 批次 {batch_num}/{total_batches} 失败: {e}")
    logger.info(f"[行情] 完成，{ok}/{total} 只标的成功")


def download_financials(
    data_provider: MiniQmtDataProvider,
    symbols: list[str],
    start_date: str,
    end_date: str,
    batch_size: int,
) -> None:
    """分批下载财务数据并写入 DuckDB 缓存。"""
    total = len(symbols)
    if total == 0:
        logger.info("[财务] 无标的需要下载（ETF 无财务数据）")
        return
    start_label = start_date or "(最早)"
    logger.info(
        f"[财务] 开始下载 {total} 只股票财务数据 ({start_label} ~ {end_date})"
    )
    ok = 0
    total_batches = (total + batch_size - 1) // batch_size
    for i in range(0, total, batch_size):
        batch = symbols[i : i + batch_size]
        batch_num = i // batch_size + 1
        t0 = time.time()
        try:
            df = data_provider._fetch_financials(batch, start_date, end_date)
            if df is not None and not df.empty:
                data_provider.cache.save_financials(df)
                ok += len(batch)
            elapsed = time.time() - t0
            logger.info(
                f"[财务] 批次 {batch_num}/{total_batches} "
                f"完成 ({len(batch)} 只, {elapsed:.1f}s)"
            )
        except Exception as e:
            logger.warning(f"[财务] 批次 {batch_num}/{total_batches} 失败: {e}")
    logger.info(f"[财务] 完成，{ok}/{total} 只股票成功")


def main() -> None:
    """主函数。"""
    parser = argparse.ArgumentParser(description="下载全量数据到 DuckDB 缓存")
    parser.add_argument(
        "--universe",
        default="all",
        help="股票池类型（all/all_a/etf/csi300/csi500/sse50/csi1000，默认 all）",
    )
    parser.add_argument(
        "--start",
        default="",
        help="起始日期 YYYY-MM-DD，空字符串=最长历史（默认）",
    )
    parser.add_argument(
        "--end",
        default="",
        help="结束日期 YYYY-MM-DD，空字符串=最新（默认今天）",
    )
    parser.add_argument(
        "--skip-financial",
        action="store_true",
        help="跳过财务数据下载（仅下载行情）",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"分批下载每批数量（默认 {BATCH_SIZE}）",
    )
    args = parser.parse_args()

    end_date = args.end or date.today().strftime("%Y-%m-%d")

    logger.info("=" * 60)
    logger.info("全量数据下载脚本")
    logger.info(f"股票池: {args.universe}")
    logger.info(f"日期范围: {args.start or '(最早)'} ~ {end_date}")
    logger.info(f"批次大小: {args.batch_size}")
    logger.info("=" * 60)

    cache = DataCache()
    data_provider = MiniQmtDataProvider(cache)
    client = MiniQmtClient.get()

    if not data_provider.is_available:
        logger.error("xtquant 不可用，无法下载数据。请确保 miniQMT 客户端已连接。")
        sys.exit(1)

    date_str = end_date.replace("-", "")

    # 1. 获取股票池
    price_symbols, financial_symbols = get_universe_symbols(
        client, args.universe, date_str, cache
    )
    if not price_symbols:
        logger.error("股票池为空，终止")
        sys.exit(1)

    # 2. 下载行情数据
    download_prices(
        data_provider, price_symbols, args.start, end_date, args.batch_size
    )

    # 3. 下载财务数据（ETF 无财务数据，--skip-financial 跳过）
    if args.skip_financial:
        logger.info("[财务] 已跳过（--skip-financial）")
    else:
        download_financials(
            data_provider,
            financial_symbols,
            args.start,
            end_date,
            args.batch_size,
        )

    logger.info("=" * 60)
    logger.info(f"数据下载完成！缓存路径: {cache.db_path}")
    logger.info("=" * 60)

    with contextlib.suppress(Exception):
        cache.close()


if __name__ == "__main__":
    main()
