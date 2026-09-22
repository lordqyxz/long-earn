"""P0 复权断裂修复回归测试：原始价 + 等比前复权因子（adj_factor）。

背景（verify/adjust_break_diagnosis_20260923.md）：摄取曾以精确前复权
（dividend_type="front"）落库，锚点随查询时点漂移 → 面板负价/假动量。
修复后契约：

1. price_daily 存**原始价** + ``adj_factor``（复权价 = 原始价 × 因子）
2. ``_panel_rebuild_sql`` 物化时现算复权（COALESCE 缺因子 → 1.0 退化）
3. ``get_prices`` 读取层同样乘因子（旧引擎路径与面板语义一致）
4. ``MiniQmtClient.get_kline`` 同批双查询（none + front_ratio）产出因子；
   因子查询失败降级 1.0（等价不复权），不丢弃原始价

PG-backed 测试（共享 long_earn 库，随机 symbol 隔离 + teardown 清理）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.miniqmt_provider import MiniQmtClient
from long_earn.backtest.data.wide_panel import read_wide_panel

_DATES = ("2024-03-28", "2024-03-29", "2024-04-01", "2024-04-02")


@pytest.fixture()
def adj_cache() -> Iterator[DataCache]:
    cache = DataCache()
    yield cache
    cache.close()


def _mk_prices(symbols: list[str], factors: list[float | None]) -> pd.DataFrame:
    """构造含 adj_factor 的行情种子：close=10..13，因子各异。"""
    rows = []
    for sym, f in zip(symbols, factors, strict=False):
        for i, d in enumerate(_DATES):
            row: dict[str, Any] = {
                "symbol": sym,
                "date": d,
                "open": 10.0,
                "high": 10.5,
                "low": 9.5,
                "close": 10.0 + i,
                "volume": 1000.0,
                "is_tradable": True,
            }
            if f is not None:
                row["adj_factor"] = f
            rows.append(row)
    return pd.DataFrame(rows)


# ── 1. save_prices 持久化因子 + 缺列幂等保留 ──────────────────────────


@pytest.mark.integration
def test_save_prices_persists_adj_factor(adj_cache: DataCache) -> None:
    sym = f"AF-{uuid4().hex[:8]}.SH"
    try:
        adj_cache.save_prices(_mk_prices([sym], [2.0]))
        with adj_cache._read() as conn:
            from long_earn.backtest.data.cache import _exec

            row = _exec(
                conn,
                "SELECT close, adj_factor FROM price_daily WHERE symbol = %s "
                "AND date = '2024-03-29'",
                [sym],
            ).fetchone()
        assert row == (11.0, 2.0), "原始价与因子应分开落库"

        # 缺 adj_factor 列的二次写入：close 更新、因子保留
        update = _mk_prices([sym], [None]).iloc[:1].copy()
        update["close"] = 99.0
        adj_cache.save_prices(update)
        with adj_cache._read() as conn:
            from long_earn.backtest.data.cache import _exec

            row = _exec(
                conn,
                "SELECT close, adj_factor FROM price_daily WHERE symbol = %s "
                "AND date = '2024-03-28'",
                [sym],
            ).fetchone()
        assert row == (99.0, 2.0), "无因子列的 upsert 不应触碰既有因子"
    finally:
        with adj_cache._read() as conn:
            from long_earn.backtest.data.cache import _exec

            _exec(conn, "DELETE FROM price_daily WHERE symbol = %s", [sym])
            _exec(conn, "DELETE FROM panel_daily WHERE symbol = %s", [sym])
            _exec(conn, "DELETE FROM panel_dirty WHERE symbol = %s", [sym])


# ── 2. 物化层：panel_daily = 原始价 × COALESCE(factor,1) ─────────────


@pytest.mark.integration
def test_panel_rebuild_applies_adj_factor(adj_cache: DataCache) -> None:
    uid = uuid4().hex[:8]
    sym_f, sym_n = f"AF-{uid}.SH", f"AF-{uid}.SZ"  # 有因子 / 无因子
    try:
        adj_cache.save_prices(_mk_prices([sym_f, sym_n], [0.5, None]))
        adj_cache.rebuild_panel_symbols([sym_f, sym_n])
        with adj_cache._read() as conn:
            from long_earn.backtest.data.cache import _exec

            rows = _exec(
                conn,
                "SELECT symbol, date, close FROM panel_daily "
                "WHERE symbol = ANY(%s::varchar[]) ORDER BY symbol, date",
                [[sym_f, sym_n]],
            ).fetchall()
        by_sym: dict[str, list[float]] = {sym_f: [], sym_n: []}
        for sym, _d, close in rows:
            by_sym[sym].append(close)
        assert by_sym[sym_f] == [5.0, 5.5, 6.0, 6.5], "panel close = raw × 0.5"
        assert by_sym[sym_n] == [10.0, 11.0, 12.0, 13.0], "缺因子退化 1.0（不复权）"
    finally:
        with adj_cache._read() as conn:
            from long_earn.backtest.data.cache import _exec

            for sym in (sym_f, sym_n):
                _exec(conn, "DELETE FROM price_daily WHERE symbol = %s", [sym])
                _exec(conn, "DELETE FROM panel_daily WHERE symbol = %s", [sym])
                _exec(conn, "DELETE FROM panel_dirty WHERE symbol = %s", [sym])


# ── 3. get_prices 读取层乘因子 + 非法因子降级 ─────────────────────────


@pytest.mark.integration
def test_get_prices_applies_adj_factor(adj_cache: DataCache) -> None:
    sym = f"AF-{uuid4().hex[:8]}.SH"
    try:
        # 因子含 NaN / 0 / 正常值 → 后两者退化 1.0
        df_in = _mk_prices([sym], [2.0])
        df_in.loc[0, "adj_factor"] = np.nan
        df_in.loc[1, "adj_factor"] = 0.0
        adj_cache.save_prices(df_in)

        got = adj_cache.get_prices(
            [sym], "2024-03-01", "2024-04-30", fields=["open", "high", "low", "close"]
        )
        assert got is not None
        got = got.sort_values("date").reset_index(drop=True)
        # day0 因子 NaN → 1.0；day1 因子 0 → 1.0；day2/3 因子 2.0
        assert got["close"].tolist() == [10.0, 11.0, 24.0, 26.0]
        assert got["open"].tolist() == [10.0, 10.0, 20.0, 20.0]
        assert "adj_factor" not in got.columns, "辅助列不应透出"
    finally:
        with adj_cache._read() as conn:
            from long_earn.backtest.data.cache import _exec

            _exec(conn, "DELETE FROM price_daily WHERE symbol = %s", [sym])
            _exec(conn, "DELETE FROM panel_daily WHERE symbol = %s", [sym])
            _exec(conn, "DELETE FROM panel_dirty WHERE symbol = %s", [sym])


# ── 4. 宽表端到端：read_wide_panel 见到复权价 ─────────────────────────


@pytest.mark.integration
def test_wide_panel_prices_are_adjusted(adj_cache: DataCache) -> None:
    sym = f"AF-{uuid4().hex[:8]}.SH"
    try:
        adj_cache.save_prices(_mk_prices([sym], [0.5]))
        # read_wide_panel 有 10 天新鲜度容忍：end 需落在 price 末端 +10d 内
        wide = read_wide_panel(adj_cache, [sym], "2024-03-01", "2024-04-10")
        assert wide is not None
        closes = wide.sort("timestamp")["close"].to_numpy()
        assert np.allclose(closes, [5.0, 5.5, 6.0, 6.5])
    finally:
        with adj_cache._read() as conn:
            from long_earn.backtest.data.cache import _exec

            _exec(conn, "DELETE FROM price_daily WHERE symbol = %s", [sym])
            _exec(conn, "DELETE FROM panel_daily WHERE symbol = %s", [sym])
            _exec(conn, "DELETE FROM panel_dirty WHERE symbol = %s", [sym])


# ── 5. get_kline 同批双查询产出因子 + 失败降级 ────────────────────────


class _StubXtdata:
    """xtdata 桩：none 返回原始价，front_ratio 返回复权 close（可注入失败）。"""

    def __init__(self, fail_ratio: bool = False) -> None:
        self.fail_ratio = fail_ratio
        self.calls: list[str] = []

    def get_market_data_ex(self, **kw: Any) -> dict[str, pd.DataFrame]:
        dt = kw.get("dividend_type")
        self.calls.append(dt)
        if dt == "front_ratio" and self.fail_ratio:
            raise RuntimeError("stub ratio failure")
        factor = 1.0 if dt == "none" else 0.5
        out = {}
        for sym in kw["stock_list"]:
            out[sym] = pd.DataFrame(
                {
                    "time": [
                        1711584000000,  # 2024-03-28
                        1711670400000,  # 2024-03-29
                    ],
                    "open": [10.0 * factor] * 2,
                    "high": [10.5 * factor] * 2,
                    "low": [9.5 * factor] * 2,
                    "close": [10.0 * factor, 11.0 * factor],
                    "volume": [1000.0] * 2,
                    "suspendFlag": [0, 0],
                }
            )
        return out


def _with_stub_xtdata(stub: _StubXtdata) -> Iterator[MiniQmtClient]:
    client = MiniQmtClient.get()
    saved_data, saved_avail = client._xtdata, client._available
    client._xtdata, client._available = stub, True
    try:
        yield client
    finally:
        client._xtdata, client._available = saved_data, saved_avail


def test_get_kline_emits_adj_factor() -> None:
    stub = _StubXtdata()
    for client in _with_stub_xtdata(stub):
        df = client.get_kline(stock_list=["600000.SH"], period="1d")
    assert stub.calls == ["none", "front_ratio"], "应同批双查询"
    assert df["adj_factor"].tolist() == [0.5, 0.5]
    assert df["close"].tolist() == [10.0, 11.0], "落库价必须是原始价"


def test_get_kline_factor_failure_degrades_to_one() -> None:
    stub = _StubXtdata(fail_ratio=True)
    for client in _with_stub_xtdata(stub):
        df = client.get_kline(stock_list=["600000.SH"], period="1d")
    assert df["adj_factor"].tolist() == [1.0, 1.0], "因子查询失败降级不复权"
    assert df["close"].tolist() == [10.0, 11.0], "原始价不因因子失败丢弃"
