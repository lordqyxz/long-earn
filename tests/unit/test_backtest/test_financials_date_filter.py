"""get_financials 日期范围过滤测试（AUDIT-P3-08）

验证 ``DataCache.get_financials`` 的 start/end 报告期窗口过滤语义：
- 过滤区间 = ``[start, end]``（含端点，SQL 用 ``>=`` / ``<=``）
- 缺省空字符串 = 不过滤（向后兼容，旧调用方零改动）
- 空区间（start > end）生成不可满足谓词，运行时返回空

不触碰共享 PostgreSQL：单测全部走 Mock（构造查询 + 参数转发），
实际 PG 行过滤由既有集成测试（test_provider_pit_contract 往返）覆盖。
"""

from collections.abc import Callable
from unittest.mock import MagicMock

import pandas as pd

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.financial.schemas import FinancialSchemaRegistry


def _scalar_tables() -> tuple:
    """取 union 查询使用的 5 张标量表 schema 元组。"""
    return FinancialSchemaRegistry.scalar_tables()[:5]


def _make_cache(
    fetchdf_side_effect: Callable[..., pd.DataFrame],
) -> DataCache:
    """构造不连 PG 的 DataCache 桩（跳过 __init__，实例级打桩 _get_conn/_fetchdf）。"""
    cache = object.__new__(DataCache)
    cache._get_conn = MagicMock()  # type: ignore[method-assign]
    cache._fetchdf = MagicMock(side_effect=fetchdf_side_effect)
    return cache


class TestBuildUnionFinancialsQueryDateFilter:
    """_build_union_financials_query 的日期过滤谓词构造（纯函数，无 DB）。"""

    def test_dates_add_inclusive_predicates_and_params(self):
        """传入 start/end 时生成含端点的 [start, end] 过滤谓词。"""
        symbols = ["000001.SZ", "600519.SH"]
        tables = _scalar_tables()
        query, params = DataCache._build_union_financials_query(
            tables, symbols, None, "2024-01-01", "2024-12-31"
        )
        # 含端点：必须用 >= / <=，不得用 > / <
        assert "report_date >= %s::date" in query
        assert "report_date <= %s::date" in query
        # 参数顺序：各表 symbol 占位 + 末尾两个日期
        expected = list(symbols) * len(tables) + ["2024-01-01", "2024-12-31"]
        assert params == expected

    def test_start_only_appends_lower_bound(self):
        """只传 start 时仅追加下界谓词。"""
        symbols = ["000001.SZ"]
        tables = _scalar_tables()
        query, params = DataCache._build_union_financials_query(
            tables, symbols, None, "2024-01-01", ""
        )
        assert "report_date >= %s::date" in query
        assert "report_date <= %s::date" not in query
        assert params == list(symbols) * len(tables) + ["2024-01-01"]

    def test_end_only_appends_upper_bound(self):
        """只传 end 时仅追加上界谓词。"""
        symbols = ["000001.SZ"]
        tables = _scalar_tables()
        query, params = DataCache._build_union_financials_query(
            tables, symbols, None, "", "2024-12-31"
        )
        assert "report_date <= %s::date" in query
        assert "report_date >= %s::date" not in query
        assert params == list(symbols) * len(tables) + ["2024-12-31"]

    def test_no_dates_backward_compatible(self):
        """缺省不传日期：不追加任何过滤谓词，参数仅 symbol（向后兼容）。"""
        symbols = ["000001.SZ"]
        tables = _scalar_tables()
        query, params = DataCache._build_union_financials_query(tables, symbols, None)
        assert "report_date >=" not in query
        assert "report_date <=" not in query
        # 外层（union 之后、GROUP BY 之前）不得追加 WHERE——内层每张表自身的
        # ``WHERE symbol IN (...)`` 恰好 len(tables) 个，多出即说明加了日期过滤。
        assert query.split("GROUP BY")[0].count("WHERE") == len(tables)
        assert params == list(symbols) * len(tables)

    def test_empty_range_keeps_both_predicates(self):
        """空区间（start > end）：谓词仍完整生成（运行时不可满足 → 空结果）。"""
        symbols = ["000001.SZ"]
        tables = _scalar_tables()
        query, params = DataCache._build_union_financials_query(
            tables, symbols, None, "2024-12-31", "2024-01-01"
        )
        assert "report_date >= %s::date" in query
        assert "report_date <= %s::date" in query
        assert params[-2:] == ["2024-12-31", "2024-01-01"]


class TestGetFinancialsForwardsDateFilter:
    """get_financials 将日期参数转发到查询构造（Mock _fetchdf 捕获）。"""

    @staticmethod
    def _sample_financials_df() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "symbol": ["000001.SZ"],
                "report_date": pd.to_datetime(["2024-03-31"]),
                "announce_date": pd.to_datetime(["2024-04-25"]),
                "revenue": [100.0],
            }
        )

    def test_forwards_start_end_to_query(self):
        """start/end 被透传进 SQL 谓词与绑定参数。"""
        captured: dict = {}

        def fake_fetchdf(conn: object, query: str, params: list) -> pd.DataFrame:
            captured["query"] = query
            captured["params"] = params
            return self._sample_financials_df()

        cache = _make_cache(fake_fetchdf)
        result = cache.get_financials(
            ["000001.SZ"], start_date="2024-01-01", end_date="2024-12-31"
        )
        assert result is not None and not result.empty
        assert "report_date >= %s::date" in captured["query"]
        assert "report_date <= %s::date" in captured["query"]
        assert "2024-01-01" in captured["params"]
        assert "2024-12-31" in captured["params"]

    def test_no_dates_does_not_forward_predicates(self):
        """不传日期：无日期谓词，参数仅 symbol 占位（旧行为不变）。"""
        captured: dict = {}

        def fake_fetchdf(conn: object, query: str, params: list) -> pd.DataFrame:
            captured["query"] = query
            captured["params"] = params
            return self._sample_financials_df()

        cache = _make_cache(fake_fetchdf)
        cache.get_financials(["000001.SZ"])
        assert "report_date >=" not in captured["query"]
        assert "report_date <=" not in captured["query"]
        # 参数只含 symbol 占位（5 张表各一份），无日期绑定
        assert captured["params"] == ["000001.SZ"] * 5

    def test_empty_range_returns_none_when_no_rows(self):
        """空区间：谓词不可满足，查询返回空帧 → get_financials 返回 None。"""
        cache = _make_cache(lambda conn, query, params: pd.DataFrame())
        result = cache.get_financials(
            ["000001.SZ"], start_date="2024-12-31", end_date="2024-01-01"
        )
        assert result is None

    def test_empty_symbols_short_circuits(self):
        """空股票列表直接返回 None，不触发查询。"""
        cache = _make_cache(lambda conn, query, params: pd.DataFrame())
        assert cache.get_financials([]) is None
