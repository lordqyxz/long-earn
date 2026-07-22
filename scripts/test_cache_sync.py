#!/usr/bin/env python3
"""冒烟测试：验证 cache_sync 模块的启动同步 + 纯缓存切换流程。

用 csi500 股票池（500 只）做最小验证，避免全量 5200+ 股票同步太久。

用法:
    uv run python scripts/test_cache_sync.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from loguru import logger  # noqa: E402

logger.remove()
logger.add(sys.stderr, level="INFO")

from long_earn.services.cache_sync import (  # noqa: E402
    is_cache_only,
    sync_data_cache,
)


def main() -> None:
    print("=" * 60)
    print("cache_sync 冒烟测试")
    print("=" * 60)

    # 确保起始状态非纯缓存
    if is_cache_only():
        print("⚠ 当前已处于纯缓存模式，测试前先清理环境变量")
        os.environ.pop("LONG_EARN_CACHE_ONLY", None)
        from long_earn.backtest.data.miniqmt_provider import MiniQmtClient

        client = MiniQmtClient.get()
        client._available = None
        client._xtdata = None

    print(f"初始 is_cache_only: {is_cache_only()}")

    # 用 csi500 做最小同步验证（避免全量太久）
    print("\n--- 调用 sync_data_cache(universe='csi500') ---")
    t0 = time.time()
    result = sync_data_cache(
        universe="csi500",
        end_date="",  # 默认今天
        skip_financial=True,  # 仅同步行情，加速测试
    )
    t1 = time.time()
    print(f"同步耗时: {t1 - t0:.1f}s")
    print(f"同步结果: {result}")

    # 验证同步后已切换到纯缓存模式
    print(f"\n同步后 is_cache_only: {is_cache_only()}")
    if not is_cache_only():
        print("❌ 同步后未切换到纯缓存模式")
        return

    # 验证 MiniQmtClient.is_available 现在返回 False
    from long_earn.backtest.data.miniqmt_provider import MiniQmtClient

    client = MiniQmtClient.get()
    available = client.is_available
    print(f"同步后 MiniQmtClient.is_available: {available}")
    if available:
        print("❌ 纯缓存模式下 xtquant 仍标记为可用")
        return

    print("\n✅ 冒烟测试通过：同步 → 切换纯缓存 → xtquant 禁用")


if __name__ == "__main__":
    main()
