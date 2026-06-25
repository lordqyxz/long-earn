#!/usr/bin/env python3
"""下载最新行情与财务数据到 DuckDB 缓存。

复用项目自身的 MiniQmtUniverseProvider / MiniQmtDataProvider，
将数据写入 ~/.long_earn/backtest_cache.duckdb，供回测引擎离线使用。

用法:
    uv run python scripts/download_data.py                          # 默认 CSI300
    uv run python scripts/download_data.py --universe csi300+csi500  # 沪深300+中证500
    uv run python scripts/download_data.py --start 2022-01-01 --end 2026-06-25
"""

from __future__ import annotations

import argparse
import contextlib
import sys
import time
from pathlib import Path

from loguru import logger

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from long_earn.backtest.data.miniqmt_provider import (  # noqa: E402
    MiniQmtDataProvider,
    MiniQmtUniverseProvider,
)
from long_earn.services.backtest_service import PandasToPolarsProvider  # noqa: E402

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


def download_universe(
    universe_provider: MiniQmtUniverseProvider,
    universe_type: str,
    date_str: str,
) -> list[str]:
    """下载股票池成分股并写入缓存。"""
    logger.info(f"[股票池] 获取 {universe_type} 成分股...")
    symbols = universe_provider.get_symbols(universe_type, date_str)
    if not symbols:
        logger.error(f"[股票池] {universe_type} 成分股为空，检查 xtquant 是否连接")
        return []
    logger.info(f"[股票池] {universe_type}: {len(symbols)} 只")
    return symbols


def download_prices(
    data_provider: MiniQmtDataProvider,
    symbols: list[str],
    start_date: str,
    end_date: str,
) -> None:
    """分批下载行情数据并写入 DuckDB 缓存。"""
    total = len(symbols)
    logger.info(f"[行情] 开始下载 {total} 只股票行情 ({start_date} ~ {end_date})")
    xt_symbols = PandasToPolarsProvider._format_symbols(symbols)
    ok = 0
    for i in range(0, len(xt_symbols), BATCH_SIZE):
        batch = xt_symbols[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
        t0 = time.time()
        try:
            df = data_provider._fetch_kline(batch, start_date, end_date)
            if df is not None and not df.empty:
                data_provider.cache.save_prices(df)
                ok += len(batch)
            logger.info(
                f"[行情] 批次 {batch_num}/{total_batches} "
                f"完成 ({len(batch)} 只, {time.time() - t0:.1f}s)"
            )
        except Exception as e:
            logger.warning(f"[行情] 批次 {batch_num} 失败: {e}")
    logger.info(f"[行情] 完成，{ok}/{total} 只股票成功")


def download_financials(
    data_provider: MiniQmtDataProvider,
    symbols: list[str],
    start_date: str,
    end_date: str,
) -> None:
    """分批下载财务数据并写入 DuckDB 缓存。"""
    total = len(symbols)
    logger.info(f"[财务] 开始下载 {total} 只股票财务数据 ({start_date} ~ {end_date})")
    xt_symbols = PandasToPolarsProvider._format_symbols(symbols)
    ok = 0
    for i in range(0, len(xt_symbols), BATCH_SIZE):
        batch = xt_symbols[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
        t0 = time.time()
        try:
            df = data_provider._fetch_financials(batch, start_date, end_date)
            if df is not None and not df.empty:
                data_provider.cache.save_financials(df)
                ok += len(batch)
            logger.info(
                f"[财务] 批次 {batch_num}/{total_batches} "
                f"完成 ({len(batch)} 只, {time.time() - t0:.1f}s)"
            )
        except Exception as e:
            logger.warning(f"[财务] 批次 {batch_num} 失败: {e}")
    logger.info(f"[财务] 完成，{ok}/{total} 只股票成功")


def main() -> None:
    """主函数。"""
    parser = argparse.ArgumentParser(description="下载最新数据到 DuckDB 缓存")
    parser.add_argument(
        "--universe",
        default="csi300",
        help="股票池类型（csi300/csi500/sse50/csi1000/all_a，支持 + 组合）",
    )
    parser.add_argument("--start", default="2020-01-01", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", default="2026-06-25", help="结束日期 YYYY-MM-DD")
    parser.add_argument(
        "--skip-financial",
        action="store_true",
        help="跳过财务数据下载（仅下载行情）",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("数据下载脚本")
    logger.info(f"股票池: {args.universe}")
    logger.info(f"日期范围: {args.start} ~ {args.end}")
    logger.info("=" * 60)

    # 初始化提供者
    universe_provider = MiniQmtUniverseProvider()
    data_provider = MiniQmtDataProvider()

    if not data_provider.is_available:
        logger.error("xtquant 不可用，无法下载数据。请确保 miniQMT 客户端已连接。")
        sys.exit(1)

    date_str = args.end.replace("-", "")

    # 1. 下载股票池
    symbols = download_universe(universe_provider, args.universe, date_str)
    if not symbols:
        logger.error("股票池为空，终止")
        sys.exit(1)

    # 2. 下载行情数据
    download_prices(data_provider, symbols, args.start, args.end)

    # 3. 下载财务数据
    if not args.skip_financial:
        download_financials(data_provider, symbols, args.start, args.end)

    logger.info("=" * 60)
    logger.info("数据下载完成！缓存路径: ~/.long_earn/backtest_cache.duckdb")
    logger.info("=" * 60)

    # 关闭缓存连接
    with contextlib.suppress(Exception):
        data_provider.cache.close()


if __name__ == "__main__":
    main()
