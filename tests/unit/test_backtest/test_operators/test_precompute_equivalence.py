"""预计算 ≡ 逐 bar 截断计算的等价性测试（皇冠测试）。

发散 = 因果性证明被违反 = bug。含 ewm（ema）因子——两侧均从面板
首行起算，必须逐值相等。等价性同时是 ADR-009 因果性证明
（prove_causality）的运行时验证。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import polars as pl
import pytest

from long_earn.backtest.engine.operator_executor import (
    OperatorStrategyExecutor,
    precompute_factors,
    resolve_factor_step,
    resolve_signal_step,
)


def _synthetic_panel(seed: int, n_days: int = 60, n_symbols: int = 8) -> pl.DataFrame:
    """确定性合成面板：seed 控制随机游走价格。"""
    rng = random.Random(seed)
    base = datetime(2024, 1, 1)
    rows = []
    price = {f"S{i}": 100.0 + i for i in range(n_symbols)}
    for d in range(n_days):
        ts = base + timedelta(days=d)
        for i in range(n_symbols):
            sym = f"S{i}"
            price[sym] *= 1 + rng.uniform(-0.03, 0.03)
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": sym,
                    "close": round(price[sym], 4),
                }
            )
    return pl.DataFrame(rows).sort("timestamp")


def _specs() -> tuple[list, list]:
    """因子链：有限窗口（sma/returns）+ ewm（ema）；信号：截面内排名。"""
    factors = [
        resolve_factor_step(
            {"op": "sma", "alias": "f_sma", "params": {"field": "close", "window": 5}}
        ),
        resolve_factor_step(
            {"op": "ema", "alias": "f_ema", "params": {"field": "close", "span": 8}}
        ),
        resolve_factor_step(
            {
                "op": "returns",
                "alias": "f_ret",
                "params": {"field": "close", "period": 3},
            }
        ),
    ]
    signals = [
        resolve_signal_step(
            {
                "type": "operator",
                "op": "rank_top",
                "params": {"field": "f_sma", "top": 3},
            }
        )
    ]
    return factors, signals


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_precompute_equals_incremental(seed: int) -> None:
    """每个时间戳：预计算截面执行 ≡ 截断历史面板全链执行。"""
    panel = _synthetic_panel(seed)
    factors, signals = _specs()
    executor = OperatorStrategyExecutor(factors, signals)

    enriched, factor_columns = precompute_factors(factors, panel)
    assert factor_columns, "测试前提：至少产出一列因子"

    timestamps = panel["timestamp"].unique().sort().to_list()
    for ts in timestamps[10:]:  # 跳过预热期（因子窗口未满时两侧同样未满）
        # 旧语义：截断历史面板上跑 factor+signal 全链
        history = panel.filter(pl.col("timestamp") <= ts)
        legacy_sel, legacy_rationale = executor.execute_with_rationale(history, ts)
        # 新语义：预计算面板的当前截面只跑 signal
        cross = enriched.filter(pl.col("timestamp") == ts)
        new_sel, new_rationale = executor.execute_precomputed(cross, factor_columns, ts)
        assert new_sel == legacy_sel, f"seed={seed} ts={ts}: {new_sel} != {legacy_sel}"
        assert new_rationale["universe_size"] == legacy_rationale["universe_size"]
        assert new_rationale["selected_count"] == legacy_rationale["selected_count"]
        # 选中标的的因子值逐值相等（rationale.selection 含因子值）
        legacy_vals = {s["symbol"]: s for s in legacy_rationale["selection"]}
        for item in new_rationale["selection"]:
            legacy_item = legacy_vals[item["symbol"]]
            for col in factor_columns:
                assert item[col] == pytest.approx(legacy_item[col]), (
                    f"seed={seed} ts={ts} {item['symbol']}.{col}: "
                    f"{item[col]} != {legacy_item[col]}"
                )
