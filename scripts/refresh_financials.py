#!/usr/bin/env python3
"""仅强制重下财务数据（补充 CashFlow 扩展字段）。

跳过行情下载，只对全部沪深A股执行全量财务数据重下，
以填充 investing_cf / financing_cf / net_cash_change / cash_from_sales 等新列。

守护模式：若 xtquant C++ 端 SIGABRT 杀死子进程（exit code < 0），
等待数秒后自动重启，靠 INSERT OR REPLACE 幂等合并实现断点续传。

用法:
    uv run python scripts/refresh_financials.py
    uv run python scripts/refresh_financials.py --max-workers 4
    uv run python scripts/refresh_financials.py --restart-delay 10
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent


def main() -> None:
    """守护循环：以子进程运行财务重下，崩溃则重启。"""
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

    # 子进程执行内层逻辑
    cmd = [
        sys.executable,
        str(project_root / "scripts" / "_refresh_financials_inner.py"),
        *clean_argv,
    ]

    max_restarts = 30
    for attempt in range(1, max_restarts + 1):
        print(f"[守护] 第 {attempt}/{max_restarts} 次启动财务重下子进程", flush=True)
        r = subprocess.run(cmd, cwd=str(project_root))

        if r.returncode == 0:
            print("[守护] 财务重下子进程正常退出", flush=True)
            return

        if r.returncode > 0:
            # 业务错误（如 xtquant 不可用），重启无意义
            print(
                f"[守护] 子进程业务错误退出 (exit={r.returncode})，不重启",
                flush=True,
            )
            sys.exit(r.returncode)

        # exit code < 0 = 被信号杀死（SIGABRT 等），等待后重启
        print(
            f"[守护] 子进程被信号杀死 (exit={r.returncode})，"
            f"{restart_delay}s 后重启...",
            flush=True,
        )
        time.sleep(restart_delay)

    print(f"[守护] 达到最大重启次数 {max_restarts}，终止", flush=True)
    sys.exit(1)


if __name__ == "__main__":
    main()
