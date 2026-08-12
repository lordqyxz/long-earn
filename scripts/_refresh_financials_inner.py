#!/usr/bin/env python3
"""财务数据重下内层脚本（由 refresh_financials.py 守护调用）。

检查 cashflow_stmt 中哪些股票已有 investing_cf 数据（非 NULL），
仅对缺失的股票执行全量财务重下，实现断点续传。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}",
)


def main() -> None:
    import argparse

    from long_earn.backtest.data.cache import DataCache
    from long_earn.services.data_ingestion_service import DataIngestionService

    parser = argparse.ArgumentParser(description="财务数据重下内层逻辑")
    parser.add_argument("--start", type=str, default="", help="起始日期")
    args = parser.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")

    service = DataIngestionService(logger=logger)

    # 获取沪深A股列表
    logger.info("正在获取沪深A股列表...")
    _, all_symbols = service.get_universe_symbols("all_a", today)
    logger.info(f"共 {len(all_symbols)} 只 A 股")

    # 检查哪些股票已有 investing_cf 数据（断点续传）
    cache = service.cache
    try:
        conn = cache._get_conn()
        # 检查 cashflow_stmt 表是否有 investing_cf 列
        col_check = conn.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_name='cashflow_stmt' AND column_name='investing_cf'"
        ).fetchone()[0]
        if col_check == 0:
            logger.warning("cashflow_stmt 表无 investing_cf 列，全部重下")
            pending = all_symbols
        else:
            # 查询已有 investing_cf 非 NULL 的股票
            done_rows = conn.execute(
                "SELECT DISTINCT symbol FROM cashflow_stmt "
                "WHERE investing_cf IS NOT NULL"
            ).fetchall()
            done_set = {r[0] for r in done_rows}
            pending = [s for s in all_symbols if s not in done_set]
            logger.info(
                f"断点续传: {len(done_set)} 只已完成，"
                f"{len(pending)} 只待下载"
            )
    except Exception as e:
        logger.warning(f"查询已完成股票失败 ({e})，全部重下")
        pending = all_symbols

    if not pending:
        logger.info("全部股票已有现金流数据，无需下载")
        with __import__("contextlib").suppress(Exception):
            cache.close()
        return

    # 全量下载财务数据（batch_size=100 与 _FINANCIAL_BATCH 一致）
    logger.info(f"开始下载 {len(pending)} 只股票的财务数据...")
    service.download_financials(
        pending,
        args.start,
        today,
        batch_size=100,
        max_workers=4,
    )

    logger.info("=" * 60)
    logger.info("财务数据下载完成！")
    logger.info(f"缓存路径: {cache.db_path}")
    logger.info("=" * 60)

    with __import__("contextlib").suppress(Exception):
        cache.close()


if __name__ == "__main__":
    main()
