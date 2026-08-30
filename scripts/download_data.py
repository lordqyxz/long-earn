#!/usr/bin/env python3
"""从 miniQMT 增量同步数据到 PostgreSQL 缓存主数据层（薄入口）。

核心逻辑位于 long_earn.services.data_ingestion_service，
本脚本以子进程方式运行 typer CLI 的 download 子命令，并加守护重启：
若 xtquant C++ 端 SIGABRT 杀死子进程（exit code < 0），等待数秒后自动重启。
智能模式（默认）会靠缓存检测跳过已写入的股票，实现断点续传。

用法:
    # 智能增量下载（默认，断点续传）：行情按交易日精确补齐，财务按公告日阈值判定
    uv run python scripts/download_data.py

    # 强制全量重下（无断点，除非靠缓存检测）
    uv run python scripts/download_data.py --full

    # 指定日期范围
    uv run python scripts/download_data.py --start 2010-01-01 --end 2026-06-28

    # 跳过财务
    uv run python scripts/download_data.py --skip-financial

    # 自定义守护重启间隔（秒，默认 5）
    uv run python scripts/download_data.py --restart-delay 10
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))


def _rebuild_wide_panel() -> None:
    """数据更新后全量重建宽表 panel_daily（物化合并面板）。

    下载子进程的写事务已对每只更新的 symbol 原子打脏标记，读者会
    惰性增量重建——正确性不依赖本函数；守护进程空闲时立即全量重建
    只为让后续回测首读即命中宽表（省去首读等待）。
    """
    from long_earn.backtest.data.cache import DataCache

    try:
        DataCache().rebuild_panel_symbols(None)
        print("[守护] 宽表 panel_daily 全量重建完成", flush=True)
    except Exception as exc:
        # 重建失败不阻断守护流程：脏标记仍在，读者惰性重建兜底
        print(f"[守护] 宽表重建失败（读者惰性重建兜底）: {exc}", flush=True)


def main() -> None:
    """守护循环：以子进程运行 download 子命令，崩溃则重启。"""
    # 解析 --restart-delay（本脚本专属参数，需从 argv 中剔除后再透传）
    restart_delay = 5
    clean_argv: list[str] = []
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--restart-delay" and i + 1 < len(args):
            restart_delay = int(args[i + 1])
            i += 2
        else:
            clean_argv.append(args[i])
            i += 1

    # 构造 download 子命令参数
    cmd = [
        sys.executable,
        "-m",
        "long_earn",
        "sync",
        *clean_argv,
    ]

    max_restarts = 20
    for attempt in range(1, max_restarts + 1):
        print(
            f"[守护] 第 {attempt}/{max_restarts} 次启动下载子进程",
            flush=True,
        )
        # 子进程继承当前 stdout/stderr，实时输出日志
        r = subprocess.run(cmd, cwd=str(project_root), check=False)

        if r.returncode == 0:
            # 数据已更新：立即全量重建宽表（脏标记惰性重建兜底）
            _rebuild_wide_panel()
            print("[守护] 下载子进程正常退出", flush=True)
            return

        # exit code < 0 表示被信号杀死（SIGABRT=-6 等），需重启
        # exit code > 0 表示业务错误（如 xtquant 不可用），重启无意义
        if r.returncode > 0:
            print(
                f"[守护] 下载子进程业务错误退出 (exit={r.returncode})，不重启",
                flush=True,
            )
            sys.exit(r.returncode)

        print(
            f"[守护] 下载子进程被信号杀死 (exit={r.returncode})，"
            f"{restart_delay}s 后重启（智能模式将跳过已写入的缓存）",
            flush=True,
        )
        time.sleep(restart_delay)

    print(
        f"[守护] 达到最大重启次数 {max_restarts}，放弃。可重新运行本脚本继续断点续传。",
        flush=True,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
