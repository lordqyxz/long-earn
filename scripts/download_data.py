#!/usr/bin/env python3
"""从 miniQMT 增量同步数据到 DuckDB 主数据层（薄入口）。

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


def _invalidate_panel_cache() -> None:
    """数据更新后清空 merged panel 跨 run 缓存（防陈旧面板命中）。

    panel_cache 以 (symbols, start, end) 为 key 落盘 Arrow，不感知底层
    数据变更；增量下载后若不清空，回测会继续命中旧面板——数据正确性
    问题（代价只是缓存重建一次，可接受）。
    """
    from long_earn.core.storage import panel_cache_dir

    cache_dir = panel_cache_dir()
    n = 0
    for f in cache_dir.glob("*.arrow"):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass  # 竞争删除无害
    if n:
        print(f"[守护] 已清空 panel 缓存 {n} 个文件（底层数据已更新）", flush=True)


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
            # 数据已更新：清空 panel 缓存，防回测命中陈旧面板
            _invalidate_panel_cache()
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
