"""read_history_tail 窗口截断契约：恰好最近 n_bars 个交易日的全部行。

窗口截断是调仓日历史面板 O(T²)→O(T·W) 优化的正确性基础：
有限窗口算子（rolling/shift）只需 W 个交易日历史即可复现全量值。
"""

from datetime import datetime

import polars as pl
import pytest

from long_earn.backtest.engine.visibility import FutureDataError, VisibilityGuard


def _panel(days: int = 10, symbols: int = 3) -> pl.DataFrame:
    """构造 days 个交易日 × symbols 只标的的有序面板。"""
    rows = []
    for d in range(1, days + 1):
        for s in range(symbols):
            rows.append(
                {
                    "timestamp": datetime(2024, 1, d),
                    "symbol": f"S{s}",
                    "close": float(d + s),
                }
            )
    return pl.DataFrame(rows)


def test_read_history_tail_returns_exact_n_days() -> None:
    """恰好返回最近 n_bars 个交易日（含当前 bar）的全部行。"""
    guard = VisibilityGuard(_panel(days=10, symbols=3))
    guard.set_time(datetime(2024, 1, 10))
    tail = guard.read_history_tail(3)
    assert tail["timestamp"].unique().to_list() == [
        datetime(2024, 1, 8),
        datetime(2024, 1, 9),
        datetime(2024, 1, 10),
    ]
    assert tail.height == 9  # 3 天 × 3 symbol


def test_read_history_tail_clamps_at_start() -> None:
    """窗口超出面板起点时钳制到面板首行（不报错、不造数据）。"""
    guard = VisibilityGuard(_panel(days=10, symbols=3))
    guard.set_time(datetime(2024, 1, 2))
    tail = guard.read_history_tail(50)
    assert tail.height == 6  # 只有 2 天 × 3 symbol


def test_read_history_tail_n_bars_1_is_current_day_only() -> None:
    """n_bars=1 只返回当前交易日截面。"""
    guard = VisibilityGuard(_panel(days=10, symbols=3))
    guard.set_time(datetime(2024, 1, 5))
    tail = guard.read_history_tail(1)
    assert tail.height == 3
    assert (tail["timestamp"] == datetime(2024, 1, 5)).all()


def test_read_history_tail_before_init_raises() -> None:
    """时间轴未初始化时拒绝访问（与 read_history_df 同契约）。"""
    guard = VisibilityGuard(_panel())
    with pytest.raises(FutureDataError):
        guard.read_history_tail(3)
