"""主板股票池映射：main_board 须覆盖沪深主板，star_board 须为科创板。"""

from __future__ import annotations

from long_earn.backtest.data.miniqmt_provider import (
    BOARD_NAME_MAP,
    COMPOSITE_BOARD_MAP,
    DERIVED_BOARD_SPEC,
    MiniQmtUniverseProvider,
)


class _FakeCache:
    """按 index_code 返回预置成分股。"""

    def __init__(self, data: dict[str, list[str]]) -> None:
        self._data = data

    def get_universe(self, index_code: str, date: str = "") -> list[str]:
        return list(self._data.get(index_code, []))

    def save_universe(self, index_code: str, date: str, symbols: list[str]) -> None:
        self._data[index_code] = list(symbols)


def test_board_name_map_star_is_star_market() -> None:
    assert BOARD_NAME_MAP["star_board"] == "科创板"
    assert BOARD_NAME_MAP["gem"] == "创业板"
    assert BOARD_NAME_MAP["chinext"] == "创业板"
    assert BOARD_NAME_MAP["bse"] == "京市A股"


def test_main_board_composite_and_derived_specs() -> None:
    assert COMPOSITE_BOARD_MAP["main_board"] == ("sse_main", "szse_main")
    assert "main_board" not in BOARD_NAME_MAP
    assert DERIVED_BOARD_SPEC["sse_main"] == ("沪市主板", "上证A股", "科创板")
    assert DERIVED_BOARD_SPEC["szse_main"] == ("深市主板", "深证A股", "创业板")


def test_main_board_plus_gem_includes_both_mains_and_gem() -> None:
    cache = _FakeCache(
        {
            "上证A股": ["600000.SH", "600519.SH", "688001.SH"],
            "深证A股": ["000001.SZ", "000002.SZ", "300750.SZ"],
            "创业板": ["300750.SZ"],
            "科创板": ["688001.SH"],
        }
    )
    provider = MiniQmtUniverseProvider(cache)  # type: ignore[arg-type]
    provider.client._available = False

    main = provider.get_symbols("main_board", "20220101")
    assert set(main) == {"600000.SH", "600519.SH", "000001.SZ", "000002.SZ"}
    assert "688001.SH" not in main
    assert "300750.SZ" not in main

    combined = provider.get_symbols("main_board+gem", "20220101")
    assert set(combined) == {
        "600000.SH",
        "600519.SH",
        "000001.SZ",
        "000002.SZ",
        "300750.SZ",
    }

    star = provider.get_symbols("star_board", "20220101")
    assert star == ["688001.SH"]
