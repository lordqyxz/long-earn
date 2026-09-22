"""P0 复权断裂修复——原始价全量重导驱动（一次性数据操作，可重复执行、幂等）。

背景：verify/adjust_break_diagnosis_20260923.md——摄取层曾以
``dividend_type="front"``（精确前复权）落库，qfq 锚点随查询时点漂移 +
增量落库不重写历史 → 面板 851 行负 OHLC / 103 行 |ret|>50% / 600039
+1854% 假动量。修复后摄取语义为「原始价（none）+ 等比前复权因子
（front_ratio，纯乘法、最新段恒 1、恒为正）」，``_fetch_kline`` 恒定
全历史抓取保证 (原始价, 因子) 序列内部自洽。

本脚本对 price_daily 现存全部 symbol（或 --symbols 指定集）做全量重导，
把历史 qfq 价整体替换为「原始价 + adj_factor」。upsert 幂等，可中断重跑。

前置：miniQMT 客户端在线（复权变换在 QMT 查询层完成，本地数据无需重下载）。
后置：``--rebuild-panel`` 全量重建 panel_daily（原始价 × 因子现算复权），
随后用 long-earn-engine/scripts/export_panel.py 重导 Arrow，并用
dsh-long-earn-quant/temp/scan_adjust_p0.py 验证 0 异常。

用法：
  d:/dev/long-earn/.venv/Scripts/python.exe scripts/reimport_prices_raw.py \
      [--symbols 600039.SH,600188.SH] [--batch 50] [--rebuild-panel] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
import time

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.miniqmt_provider import MiniQmtDataProvider


def main() -> int:
    ap = argparse.ArgumentParser(description="原始价 + 复权因子全量重导")
    ap.add_argument("--symbols", default="", help="逗号分隔 symbol 集；缺省取 price_daily 全部")
    ap.add_argument("--batch", type=int, default=50, help="每批 symbol 数")
    ap.add_argument("--rebuild-panel", action="store_true", help="重导后全量重建 panel_daily")
    ap.add_argument("--dry-run", action="store_true", help="只列出 symbol 与批次计划，不抓取")
    args = ap.parse_args()

    cache = DataCache()
    provider = MiniQmtDataProvider(cache=cache)
    if not args.dry_run and not provider.is_available:
        print("miniQMT 不可用：请先启动并登录 miniQMT 客户端（数据转换在 QMT 查询层完成）")
        return 1

    with cache._read() as conn:
        from long_earn.backtest.data.cache import _exec

        if args.symbols:
            symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        else:
            symbols = [r[0] for r in _exec(
                conn, "SELECT DISTINCT symbol FROM price_daily ORDER BY symbol"
            ).fetchall()]
    total = len(symbols)
    print(f"待重导 symbol：{total} 只，批大小 {args.batch}")
    if args.dry_run:
        for i in range(0, total, args.batch):
            print(f"  batch {i // args.batch}: {symbols[i:i + args.batch]}")
        return 0

    t0 = time.perf_counter()
    rows_total = 0
    for bi, i in enumerate(range(0, total, args.batch)):
        batch = symbols[i : i + args.batch]
        tb = time.perf_counter()
        fetched = provider._fetch_kline(batch, start_date="", end_date="")
        if fetched is None or fetched.empty:
            print(f"[{bi}] {batch[0]}..{batch[-1]}: 抓取失败/为空，跳过（可重跑）")
            continue
        cache.save_prices(fetched)
        rows_total += len(fetched)
        print(
            f"[{bi}] {batch[0]}..{batch[-1]}: {len(fetched)} 行 "
            f"({fetched['symbol'].nunique()} 只, {time.perf_counter() - tb:.1f}s)"
        )

    print(f"重导完成：{rows_total} 行，总耗时 {time.perf_counter() - t0:.1f}s")

    if args.rebuild_panel:
        tp = time.perf_counter()
        cache.rebuild_panel_symbols(None)
        print(f"panel_daily 全量重建完成（{time.perf_counter() - tp:.1f}s）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
