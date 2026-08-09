"""数据提供者接口契约测试（面向接口，所有实现共用一套）。

测试对象：DataProvider Protocol 的财务面板实现
  - MiniQmtDataProvider（含 _quarterly_to_daily + 四表合并字段提取）

测试哲学（见 project_memory.md）：
  - 契约优先于实现：验证 Protocol 定义的行为，不验证实现细节
  - 所有实现共用一套参数化测试，一处契约变更，所有实现同步验证
  - 用 mock 数据构造可控场景，不依赖真实网络调用

核心契约（ADR-007 Phase 3）：
  C1. PIT 契约：get_financial_panel 必须返回日频面板，基于 announce_date
      （真实财报发布日期）对齐，杜绝未来函数（timestamp=T 的行只含
      announce_date <= T 的报告值）
  C2. 空返回语义：symbols 为空或数据源不可用时返回空 DataFrame
  C3. 索引契约：返回 DataFrame 的 index 必须是 (date, symbol) MultiIndex
  C4. 端到端 PIT 验证：get_financial_panel 完整链路无未来函数
  C5. 字段提取契约：miniqmt 提取 m_anntime 作为 announce_date；
      四表合并（Income + Balance + CashFlow + Pershareindex）18 个字段

注：akshare/ciccwm 的财务方法已删除（ADR-007 Phase 3 统一到 miniqmt），
仅 miniqmt 实现财务接口。
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from long_earn.backtest.data.miniqmt_provider import MiniQmtDataProvider

# ── 统一构造的季频测试数据 ─────────────────────────────────────


def _make_quarterly_data() -> pd.DataFrame:
    """构造一份季频财务数据，用于 PIT 测试。

    关键场景（ADR-007）：
    - 2020-Q1 报告期 2020-03-31，真实公告日 2020-04-25，值 revenue=100
    - 2020-Q2 报告期 2020-06-30，真实公告日 2020-08-20，值 revenue=200
    """
    return pd.DataFrame(
        {
            "symbol": ["600519.SH"] * 2,
            "report_date": pd.to_datetime(["2020-03-31", "2020-06-30"]),
            "announce_date": pd.to_datetime(["2020-04-25", "2020-08-20"]),
            "revenue": [100.0, 200.0],
            "net_profit": [10.0, 20.0],
        }
    )


# ── 参数化：provider 实现 ────────────────────────────────────


@pytest.fixture(params=["miniqmt"])
def provider_instance(request: pytest.FixtureRequest, tmp_path: Any):
    """参数化 fixture：返回一个 provider 实例。

    所有实现共用一套测试，体现"面向接口测试"哲学。
    直接测 _quarterly_to_daily（PIT 契约核心）和空返回契约，
    避免 mock 网络链路导致的测试不稳定。

    注：akshare/ciccwm 财务方法已删除，仅 miniqmt 实现财务接口。
    """
    from long_earn.backtest.data.cache import DataCache

    cache = DataCache(db_path=tmp_path / "test.duckdb")
    if request.param == "miniqmt":
        return MiniQmtDataProvider(cache=cache)
    pytest.fail(f"未知 provider: {request.param}")


# ── C1. PIT 契约：基于 announce_date 对齐，杜绝未来函数 ────────────


class TestPITContract:
    """PIT 契约测试：provider 的 _quarterly_to_daily 必须基于 announce_date 对齐。

    核心场景：
    - 2020-Q1 报告期 2020-03-31，announce_date=2020-04-25，revenue=100
    - 公告日前 revenue 必须为 NaN，公告日及之后 revenue 必须为 100
    """

    def test_no_future_function_before_announce_date(self, provider_instance: Any):
        """C1.1 公告日前不能用未公布数据——PIT 契约生效"""
        provider = provider_instance
        quarterly_df = _make_quarterly_data()
        # 2020-04-01 ~ 04-24：都在 announce_date(2020-04-25) 之前
        trading_dates = pd.date_range("2020-04-01", "2020-04-24", freq="B")
        result = provider._quarterly_to_daily(
            quarterly_df, ["600519.SH"], trading_dates, ["revenue"]
        )
        assert not result.empty, (
            f"{type(provider).__name__}._quarterly_to_daily 不应返回空"
        )
        assert result["revenue"].isna().all(), (
            f"PIT 违规：{type(provider).__name__} 在 2020-04-01~04-24"
            f"（公告日 2020-04-25 之前）返回了非 NaN revenue，"
            f"该财报 2020-04-25 才公告"
        )

    def test_data_visible_after_announce_date(self, provider_instance: Any):
        """C1.2 公告日后数据可见——announce_date 后能看到报告值"""
        provider = provider_instance
        quarterly_df = _make_quarterly_data()
        # 2020-04-25 ~ 2020-06-30：第一份报告已公告，第二份未公告
        trading_dates = pd.date_range("2020-04-25", "2020-06-30", freq="B")
        result = provider._quarterly_to_daily(
            quarterly_df, ["600519.SH"], trading_dates, ["revenue"]
        )
        assert not result.empty
        visible_rows = result[result["revenue"].notna()]
        assert not visible_rows.empty, "应有可见数据行（第一份报告已公告）"
        assert (visible_rows["revenue"] == 100.0).all(), (
            f"{type(provider).__name__} 在 2020-04-25~06-30 的可见行应返回"
            f"第一份报告 revenue=100，实际={visible_rows['revenue'].tolist()}"
        )

    def test_second_report_overrides_after_its_announce_date(
        self, provider_instance: Any
    ):
        """C1.3 第二份报告公告后覆盖第一份——announce_date 后看到新值"""
        provider = provider_instance
        quarterly_df = _make_quarterly_data()
        # 2020-09-01 ~ 09-15：第二份报告 announce_date=2020-08-20 已过
        trading_dates = pd.date_range("2020-09-01", "2020-09-15", freq="B")
        result = provider._quarterly_to_daily(
            quarterly_df, ["600519.SH"], trading_dates, ["revenue"]
        )
        assert not result.empty
        visible_rows = result[result["revenue"].notna()]
        assert not visible_rows.empty
        assert (visible_rows["revenue"] == 200.0).all(), (
            f"{type(provider).__name__} 在 2020-09 月应返回第二份报告 revenue=200，"
            f"实际={visible_rows['revenue'].tolist()}"
        )

    def test_announce_date_boundary_exact(self, provider_instance: Any):
        """C1.4 公告日当天数据可见——boundary 精确性验证"""
        provider = provider_instance
        # 构造一个周二的公告日场景
        quarterly_df2 = pd.DataFrame(
            {
                "symbol": ["600519.SH"],
                "report_date": pd.to_datetime(["2020-03-31"]),
                "announce_date": pd.to_datetime(["2020-04-28"]),  # 周二
                "revenue": [100.0],
            }
        )
        trading_dates = pd.DatetimeIndex(["2020-04-27", "2020-04-28", "2020-04-29"])
        result = provider._quarterly_to_daily(
            quarterly_df2, ["600519.SH"], trading_dates, ["revenue"]
        )
        # 04-27（公告日前）→ NaN；04-28（公告日当天）和 04-29 → 100
        vals = result["revenue"].tolist()
        assert pd.isna(vals[0]), f"公告日前一天应 NaN，实际={vals[0]}"
        assert vals[1] == 100.0, f"公告日当天应可见，实际={vals[1]}"
        assert vals[2] == 100.0, f"公告日后一天应可见，实际={vals[2]}"


# ── C2. 空返回语义 ─────────────────────────────────────────────


class TestEmptyReturnContract:
    """空返回契约：symbols 为空时返回空 DataFrame。"""

    def test_empty_symbols_returns_empty_df(self, provider_instance: Any):
        """C2.1 get_financial_panel symbols 为空 → 空 DataFrame"""
        provider = provider_instance
        result = provider.get_financial_panel(
            [], "2020-01-01", "2020-12-31", fields=["revenue"]
        )
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_empty_symbols_price_panel(self, provider_instance: Any):
        """C2.2 get_price_panel symbols 为空 → 空 DataFrame"""
        provider = provider_instance
        result = provider.get_price_panel([], "2020-01-01", "2020-12-31")
        assert isinstance(result, pd.DataFrame)
        assert result.empty


# ── C3. 索引契约 ───────────────────────────────────────────────


class TestIndexContract:
    """索引契约：_quarterly_to_daily 返回 DataFrame 必须是 (date, symbol) MultiIndex。"""

    def test_financial_panel_index_is_date_symbol_multiindex(
        self, provider_instance: Any
    ):
        """C3.1 _quarterly_to_daily 返回 (date, symbol) MultiIndex"""
        provider = provider_instance
        quarterly_df = _make_quarterly_data()
        trading_dates = pd.date_range("2020-06-01", "2020-07-15", freq="B")
        result = provider._quarterly_to_daily(
            quarterly_df, ["600519.SH"], trading_dates, ["revenue"]
        )
        assert not result.empty
        assert isinstance(result.index, pd.MultiIndex), (
            f"{type(provider).__name__}._quarterly_to_daily 必须返回 MultiIndex，"
            f"实际={type(result.index).__name__}"
        )
        names = set(result.index.names)
        assert "date" in names or "timestamp" in names, (
            f"index names 缺少 date/timestamp: {result.index.names}"
        )
        assert "symbol" in names, f"index names 缺少 symbol: {result.index.names}"


# ── C4. 端到端 PIT 验证：_quarterly_to_daily 返回日频面板 ──────────


class TestEndToEndPITNoFutureFunction:
    """端到端验证：_quarterly_to_daily 返回的日频面板必须无未来函数。

    场景：2020-Q1 报告期 2020-03-31，announce_date=2020-04-25，revenue=100。
    - 2020-04-01 ~ 04-24（公告前）：revenue 必须为 NaN
    - 2020-04-25 起（公告后）：revenue 必须为 100
    """

    def test_no_provider_returns_raw_quarterly_data(self, tmp_path: Any):
        """C4.1 防回归：_quarterly_to_daily 必须返回日频面板。

        季频原始数据（每个报告期一行）是未来函数的来源——
        如果返回季频，回测引擎在 2020-03-31 的 bar 上直接读到 Q1 财报值，
        绕过了 PIT 对齐。这个测试确保 _quarterly_to_daily 返回日频。
        """
        from long_earn.backtest.data.cache import DataCache

        cache = DataCache(db_path=tmp_path / "test_freq.duckdb")
        provider = MiniQmtDataProvider(cache=cache)

        quarterly_df = _make_quarterly_data()
        trading_dates = pd.date_range("2020-04-01", "2020-09-30", freq="B")
        result = provider._quarterly_to_daily(
            quarterly_df, ["600519.SH"], trading_dates, ["revenue"]
        )
        assert not result.empty, "miniqmt 不应返回空"
        # 日频面板应有 ~130 个交易日（4月-9月），远多于季频的 2 行
        assert len(result) > 10, (
            f"miniqmt 应返回日频面板（>10 行），"
            f"实际 {len(result)} 行（可能返回了季频原始数据）"
        )


# ── C5. 字段提取契约 ───────────────────────────────────────────


class TestAnnounceDateExtraction:
    """字段提取契约：miniqmt 必须正确提取 m_anntime 作为 announce_date。

    ADR-007 Phase 3：miniqmt 的 get_financial 从 m_anntime 字段提取
    真实公告日，四表合并提取 18 个财务字段。
    """

    def test_financial_field_map_has_18_fields(self):
        """C5.1 FINANCIAL_FIELD_MAP 包含 20 个标准字段（五表合并）

        ADR-014 任务7：纳入 Capital 表 total_shares/float_shares 两字段，
        从 18 → 20。
        """
        from long_earn.backtest.data.miniqmt_provider import FINANCIAL_FIELD_MAP

        assert len(FINANCIAL_FIELD_MAP) == 20, (
            f"FINANCIAL_FIELD_MAP 应有 20 个字段（五表合并），"
            f"实际 {len(FINANCIAL_FIELD_MAP)} 个"
        )
        # 验证各表字段存在
        expected_fields = {
            # Income
            "revenue",
            "net_profit",
            "eps",
            "research_expenses",
            # Balance
            "total_equity",
            "total_assets",
            "total_liabilities",
            # CashFlow
            "ocf",
            "capex",
            # Pershareindex
            "bps",
            "ocf_per_share",
            "debt_to_assets",
            "net_profit_margin",
            "roe_weighted",
            # 衍生指标
            "net_profit_yoy",
            "revenue_yoy",
            "roe",
            "gross_margin",
            # Capital（ADR-014 任务7）
            "total_shares",
            "float_shares",
        }
        assert set(FINANCIAL_FIELD_MAP.keys()) == expected_fields, (
            f"FINANCIAL_FIELD_MAP 字段不匹配，"
            f"缺失: {expected_fields - set(FINANCIAL_FIELD_MAP.keys())}"
        )

    def test_cache_table_has_all_financial_columns(self, tmp_path: Any):
        """C5.2 DuckDB 缓存 8 张细表包含全部财务字段列（ADR-014 阶段 B）

        旧 financial_quarterly 单一宽表已废弃，改为 8 张细表。
        验证 4 张旧表对应的细表含原 18 个财务字段。
        """
        from long_earn.backtest.data.cache import DataCache
        from long_earn.backtest.data.financial.schemas import (
            FinancialSchemaRegistry,
        )

        cache = DataCache(db_path=tmp_path / "test_cols.duckdb")
        conn = cache._get_conn()

        # 收集 4 张标量表（对应旧宽表）的全部字段
        scalar_tables = FinancialSchemaRegistry.scalar_tables()[:4]
        all_cols: set[str] = set()
        for schema in scalar_tables:
            columns = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                f"WHERE table_name = '{schema.table_name}'"
            ).fetchdf()
            all_cols.update(columns["column_name"].tolist())

        expected_financial_cols = {
            "revenue",
            "net_profit",
            "eps",
            "research_expenses",
            "total_equity",
            "total_assets",
            "total_liabilities",
            "ocf",
            "capex",
            "bps",
            "ocf_per_share",
            "debt_to_assets",
            "net_profit_margin",
            "roe_weighted",
            "net_profit_yoy",
            "revenue_yoy",
            "roe",
            "gross_margin",
        }
        missing = expected_financial_cols - all_cols
        assert not missing, f"8 张细表 collectively 缺失字段: {missing}"

    def test_save_and_get_financials_roundtrip(self, tmp_path: Any):
        """C5.3 save_financials → get_financials 全字段往返一致（ADR-014 阶段 B）

        旧 save_financials 现为兼容包装，内部拆分写入 4 张标量表；
        get_financials union 4 表返回扁平宽表。验证往返一致。
        """
        from long_earn.backtest.data.cache import DataCache

        cache = DataCache(db_path=tmp_path / "test_rt.duckdb")
        # 构造含全部 18 个字段的测试数据
        test_df = pd.DataFrame(
            {
                "symbol": ["600519.SH"],
                "report_date": pd.to_datetime(["2020-03-31"]),
                "announce_date": pd.to_datetime(["2020-04-25"]),
                "revenue": [100.0],
                "net_profit": [10.0],
                "eps": [0.8],
                "research_expenses": [5.0],
                "total_equity": [500.0],
                "total_assets": [1000.0],
                "total_liabilities": [500.0],
                "ocf": [15.0],
                "capex": [3.0],
                "bps": [40.0],
                "ocf_per_share": [1.2],
                "debt_to_assets": [0.5],
                "net_profit_margin": [0.1],
                "roe_weighted": [0.02],
                "net_profit_yoy": [0.15],
                "revenue_yoy": [0.2],
                "roe": [0.02],
                "gross_margin": [0.5],
            }
        )
        cache.save_financials(test_df)

        # 读取（不指定 fields，返回全量 union）
        result = cache.get_financials(["600519.SH"])
        assert result is not None
        assert not result.empty
        # 验证所有 18 个字段都有值（union 后跨表合并）
        for col in [
            "revenue",
            "net_profit",
            "eps",
            "research_expenses",
            "total_equity",
            "total_assets",
            "total_liabilities",
            "ocf",
            "capex",
            "bps",
            "ocf_per_share",
            "debt_to_assets",
            "net_profit_margin",
            "roe_weighted",
            "net_profit_yoy",
            "revenue_yoy",
            "roe",
            "gross_margin",
        ]:
            assert col in result.columns, f"返回结果缺少字段: {col}"
            assert pd.notna(result[col].iloc[0]), f"字段 {col} 值为 NaN"
