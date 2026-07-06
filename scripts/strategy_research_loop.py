#!/usr/bin/env python3
"""自主策略研究循环（薄入口，等价于 ``long-earn research``）。

核心逻辑位于 long_earn.services.strategy_research_service，
本脚本仅注入 research 子命令后委托给统一 typer CLI。

用法:
    uv run python scripts/strategy_research_loop.py
    uv run python scripts/strategy_research_loop.py "基于净利润增长的选股策略"
    uv run python scripts/strategy_research_loop.py "低波动率大盘选股思路" --max-rounds 5
"""

from __future__ import annotations

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from long_earn.cli import app  # noqa: E402


if __name__ == "__main__":
    # 注入 research 子命令，后续参数原样透传
    sys.argv = [sys.argv[0], "research", *sys.argv[1:]]
    app()
