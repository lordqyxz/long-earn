"""CompositeDataProvider 降级链集成测试

验证多源数据提供者在各种可用性组合下的行为：
- 空 symbols 直接返回空 DF（不触发任何 provider）
- 所有 provider 都返回空时，返回空 DF（不抛、不崩）
- 日期格式标准化（YYYYMMDD → YYYY-MM-DD）

这是数据层契约的"接口集成"层校验，不要求真实数据源在线（CI 友好）。
依赖外部服务（miniqmt / ciccwm / akshare）的真实拉取由对应单测覆盖。
"""

from typing import Any

import pandas as pd
import pytest

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.provider import CompositeDataProvider


class _StubProvider:
    """无数据 stub：模拟"provider 可加载但返回空 DataFrame"的常见场景"""

    is_available = False

    def get_price_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        return pd.DataFrame()

    def get_financial_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        return pd.DataFrame()

    def get_merged_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        price_fields: list[str] | None = None,
        financial_fields: list[str] | None = None,
    ) -> pd.DataFrame:
        return pd.DataFrame()


@pytest.fixture
def composite_all_empty(tmp_path: Any) -> CompositeDataProvider:
    """构造所有 provider 都返回空的 CompositeDataProvider（CI 友好降级）"""
    cache = DataCache(db_path=str(tmp_path / "test_cache.duckdb"))
    cp = CompositeDataProvider(cache=cache)
    # 强制注入 stub，跳过真实 miniqmt/ciccwm/akshare 加载
    cp._miniqmt = _StubProvider()  # type: ignore[assignment]
    cp._ciccwm = _StubProvider()  # type: ignore[assignment]
    cp._akshare = _StubProvider()  # type: ignore[assignment]
    cp._miniqmt_available = False
    cp._ciccwm_available = False
    cp._akshare_available = False
    return cp


class TestCompositeDataProviderDegradation:
    """全 provider 不可用时的降级行为"""

    def test_empty_symbols_returns_empty_df_for_price(
        self, composite_all_empty: CompositeDataProvider
    ):
        """空 symbols 列表应直接返回空 DF，不触发任何 provider"""
        df = composite_all_empty.get_price_panel([], "2024-01-01", "2024-12-31")
        assert df.empty

    def test_empty_symbols_returns_empty_df_for_financial(
        self, composite_all_empty: CompositeDataProvider
    ):
        """空 symbols 列表应直接返回空 DF（财务面板）"""
        df = composite_all_empty.get_financial_panel(
            [], "2024-01-01", "2024-12-31"
        )
        assert df.empty

    def test_all_providers_empty_returns_empty_df_for_price(
        self, composite_all_empty: CompositeDataProvider
    ):
        """所有 provider 都返回空时，不应抛异常，应返回空 DF"""
        df = composite_all_empty.get_price_panel(
            ["600519.SH"], "2024-01-01", "2024-12-31"
        )
        assert df.empty

    def test_all_providers_empty_returns_empty_df_for_financial(
        self, composite_all_empty: CompositeDataProvider
    ):
        """所有 provider 都返回空时，财务面板亦不应抛异常"""
        df = composite_all_empty.get_financial_panel(
            ["600519.SH"], "2024-01-01", "2024-12-31"
        )
        assert df.empty

    def test_compact_date_normalized_to_iso(
        self, composite_all_empty: CompositeDataProvider
    ):
        """YYYYMMDD 紧凑日期应被标准化为 YYYY-MM-DD（DuckDB 缓存要求）"""
        normalized = CompositeDataProvider._normalize_date("20240115")
        assert normalized == "2024-01-15"
        # 已是 ISO 格式则保持不变
        assert CompositeDataProvider._normalize_date("2024-01-15") == "2024-01-15"
        # 空字符串原样返回
        assert CompositeDataProvider._normalize_date("") == ""
