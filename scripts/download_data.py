#!/usr/bin/env python3
"""下载全量行情与财务数据到 DuckDB 缓存（薄入口，等价于 ``long-earn download``）。

核心逻辑位于 long_earn.services.data_ingestion_service，
本脚本仅注入 download 子命令后委托给统一 typer CLI。

用法:
    # 全量下载（A股行情+财务 + ETF行情）
    uv run python scripts/download_data.py

    # 仅 A 股
    uv run python scripts/download_data.py --universe all_a

    # 指定日期范围 + 跳过财务
    uv run python scripts/download_data.py --start 2010-01-01 --end 2026-06-28 --skip-financial
"""

from __future__ import annotations

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from long_earn.cli import app  # noqa: E402


if __name__ == "__main__":
    # 注入 download 子命令，后续参数原样透传
    sys.argv = [sys.argv[0], "download", *sys.argv[1:]]
    app()
