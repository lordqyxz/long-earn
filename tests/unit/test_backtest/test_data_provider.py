"""数据层未来函数防护测试

财务数据 _quarterly_to_daily 必须用"披露日"而非"报告期截止日"作为可见日期，
否则回测会在截止日次日就用上未公布数据 → 经典未来函数泄漏，违反 ADR-005 金融级可信。

get_merged_panel ffill 前必须先 sort_index，否则 outer merge 后行序乱，
groupby.ffill 会用"原始行序"填充——可能拿未来值填到过去，又一处隐蔽未来函数泄漏。
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from long_earn.backtest.data.ciccwm_provider import CiccwmDataProvider
from long_earn.backtest.data.miniqmt_provider import MiniQmtDataProvider
from long_earn.backtest.data.provider import CompositeDataProvider

# 复权一致性测试依赖真实 PostgreSQL 存储；其余测试用 _StubCache 不连库。
# 用函数级 skipif（模块级会误跳过纯逻辑测试）。


def _pg_available() -> bool:
    """探测 PostgreSQL 是否可连（不可达时复权测试跳过）。"""
    from long_earn.core.pg import pg_version

    try:
        pg_version()
        return True
    except Exception:
        return False


_PG_SKIP = pytest.mark.skipif(not _pg_available(), reason="PostgreSQL 服务不可用")

# ── 测试桩 ───────────────────────────────────────────────────────────────


class _StubCache:
    """测试用空 cache 桩（_quarterly_to_daily 不依赖 cache）。"""

    def __init__(self) -> None:
        pass


class _StubMqProvider:
    """注入 CompositeDataProvider 的桩 miniqmt 后端。

    返回构造好的 price/fin 面板，验证 Composite 的 merge+ffill 排序契约。
    """

    def __init__(self, price_df: pd.DataFrame, fin_df: pd.DataFrame) -> None:
        self._price = price_df
        self._fin = fin_df

    def get_price_panel(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        return self._price

    def get_financial_panel(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        return self._fin


# ── 参数化：实现 merge + groupby.ffill 的 provider ────────────────────

# 仅 MiniQmtDataProvider 与 CompositeDataProvider 在 get_merged_panel 中
# 执行 merge + groupby(symbol).ffill 逻辑；ciccwm/akshare 的 get_merged_panel
# 仅委托 get_price_panel（ADR-007 Phase 3 财务统一到 miniqmt，无 ffill），
# ffill 排序契约对它们 N/A，见 TestMergedPanelDelegationContract。
FFILL_PROVIDERS = ["miniqmt", "composite"]


def _make_ffill_provider(
    kind: str, price_df: pd.DataFrame, fin_df: pd.DataFrame
) -> Any:
    """构造一个 provider 实例，get_merged_panel 内 merge+ffill 数据可控。

    Args:
        kind: ``"miniqmt"`` 直接 monkey-patch 取数器；
              ``"composite"`` 注入桩 miniqmt 后端到 CompositeDataProvider。
        price_df: get_price_panel 返回的行情面板
        fin_df: get_financial_panel 返回的财务面板
    """
    if kind == "miniqmt":
        provider = MiniQmtDataProvider.__new__(MiniQmtDataProvider)
        # monkey-patch 底层取数器，强行返回测试数据
        provider.cache = _StubCache()  # type: ignore[assignment]
        provider.client = _StubCache()  # type: ignore[assignment]
        provider.get_price_panel = (  # type: ignore[method-assign]
            lambda *_a, **_k: price_df
        )
        provider.get_financial_panel = (  # type: ignore[method-assign]
            lambda *_a, **_k: fin_df
        )
        return provider
    if kind == "composite":
        stub_mq = _StubMqProvider(price_df, fin_df)
        return CompositeDataProvider(  # type: ignore[arg-type]
            cache=_StubCache(), miniqmt_provider=stub_mq
        )
    raise ValueError(f"未知 provider kind: {kind}")


# ── ffill 排序契约：merge + groupby.ffill 的 provider ─────────────────


class TestMergedPanelFfillSorted:
    """get_merged_panel ffill 前必须先 sort_index，否则 outer merge 后行序乱，
    ffill 会用未来值填到过去——典型的隐蔽未来函数泄漏。

    参数化覆盖实现了 merge + groupby.ffill 的两个 provider：
      - miniqmt: MiniQmtDataProvider
      - composite: CompositeDataProvider
    """

    @pytest.mark.parametrize("kind", FFILL_PROVIDERS)
    def test_ffill_does_not_pull_future_into_past(self, kind: str):
        """构造场景：财务数据只有 2023-06-01 的 ROE=0.2，
        若 ffill 在 sort 之前跑，可能把 0.2 填进 2023-04-01。"""
        # price_df：包含 4 月、5 月、6 月，ROE 列没有
        price_dates = pd.to_datetime(["2023-04-01", "2023-05-01", "2023-06-01"])
        price_df = pd.DataFrame(
            {
                "open": [10.0, 11.0, 12.0],
                "close": [10.5, 11.5, 12.5],
            },
            index=pd.MultiIndex.from_product(
                [price_dates, ["000001"]], names=["date", "symbol"]
            ),
        )

        # fin_df：仅 6-01 有 ROE=0.2
        fin_df = pd.DataFrame(
            {"roe": [0.2]},
            index=pd.MultiIndex.from_tuples(
                [(pd.Timestamp("2023-06-01"), "000001")], names=["date", "symbol"]
            ),
        )

        provider = _make_ffill_provider(kind, price_df, fin_df)
        result = provider.get_merged_panel(
            symbols=["000001"],
            start_date="2023-04-01",
            end_date="2023-06-30",
        )

        sym = result.xs("000001", level="symbol")
        # 4-01 / 5-01: ROE 仍应是 NaN（未来数据不能填到过去）
        assert pd.isna(sym.loc[pd.Timestamp("2023-04-01"), "roe"])
        assert pd.isna(sym.loc[pd.Timestamp("2023-05-01"), "roe"])
        # 6-01: ROE 应是 0.2
        assert sym.loc[pd.Timestamp("2023-06-01"), "roe"] == 0.2

    @pytest.mark.parametrize("kind", FFILL_PROVIDERS)
    def test_ffill_propagates_old_value_forward(self, kind: str):
        """合法场景：4-01 的 ROE=0.1 应被前向填充到 5-01 / 6-01。"""
        price_dates = pd.to_datetime(["2023-04-01", "2023-05-01", "2023-06-01"])
        price_df = pd.DataFrame(
            {"close": [10.0, 11.0, 12.0]},
            index=pd.MultiIndex.from_product(
                [price_dates, ["000001"]], names=["date", "symbol"]
            ),
        )
        fin_df = pd.DataFrame(
            {"roe": [0.1]},
            index=pd.MultiIndex.from_tuples(
                [(pd.Timestamp("2023-04-01"), "000001")], names=["date", "symbol"]
            ),
        )

        provider = _make_ffill_provider(kind, price_df, fin_df)
        result = provider.get_merged_panel(
            symbols=["000001"],
            start_date="2023-04-01",
            end_date="2023-06-30",
        )

        sym = result.xs("000001", level="symbol")
        # ROE 沿时间向后传播
        assert sym.loc[pd.Timestamp("2023-04-01"), "roe"] == 0.1
        assert sym.loc[pd.Timestamp("2023-05-01"), "roe"] == 0.1
        assert sym.loc[pd.Timestamp("2023-06-01"), "roe"] == 0.1

    @pytest.mark.parametrize("kind", FFILL_PROVIDERS)
    def test_unsorted_price_does_not_leak_future(self, kind: str):
        """关键回归测试：构造乱序行情面板（未来日期在前，过去日期在后），
        outer merge 后行序为 [6-01(roe=0.2), 4-01(NaN)]。
        若 ffill 在 sort_index 之前跑，6-01 的 0.2 会填进 4-01 → 未来函数泄漏。
        修复后（sort_index 在 ffill 前）4-01 保持 NaN。"""
        # price_df：行序倒序（6-01 在 4-01 之前）——模拟 outer merge 乱序场景
        price_df = pd.DataFrame(
            {"close": [12.5, 10.5]},
            index=pd.MultiIndex.from_tuples(
                [
                    (pd.Timestamp("2023-06-01"), "000001"),
                    (pd.Timestamp("2023-04-01"), "000001"),
                ],
                names=["date", "symbol"],
            ),
        )
        # fin_df：仅 6-01 有 ROE=0.2
        fin_df = pd.DataFrame(
            {"roe": [0.2]},
            index=pd.MultiIndex.from_tuples(
                [(pd.Timestamp("2023-06-01"), "000001")], names=["date", "symbol"]
            ),
        )

        provider = _make_ffill_provider(kind, price_df, fin_df)
        result = provider.get_merged_panel(
            symbols=["000001"],
            start_date="2023-04-01",
            end_date="2023-06-30",
        )

        sym = result.xs("000001", level="symbol")
        # 4-01: ROE 必须是 NaN（6-01 的未来值不能填到过去）
        assert pd.isna(sym.loc[pd.Timestamp("2023-04-01"), "roe"]), (
            f"{kind}: ffill 前未 sort_index 导致 6-01 的 ROE=0.2 泄漏到 4-01"
        )
        # 6-01: ROE 应是 0.2
        assert sym.loc[pd.Timestamp("2023-06-01"), "roe"] == 0.2

    @pytest.mark.parametrize("kind", FFILL_PROVIDERS)
    def test_price_not_ffilled_fin_still_ffilled(self, kind: str):
        """价格列缺失保持 NaN；财务列仍前向填充（AUDIT-P1-02）。"""
        # 行情仅 5-01、6-01 有 close；4-01 无行情
        price_dates = pd.to_datetime(["2023-05-01", "2023-06-01"])
        price_df = pd.DataFrame(
            {"close": [11.0, 12.0]},
            index=pd.MultiIndex.from_product(
                [price_dates, ["000001"]], names=["date", "symbol"]
            ),
        )
        # 财务 4-01、6-01 有 ROE
        fin_dates = pd.to_datetime(["2023-04-01", "2023-06-01"])
        fin_df = pd.DataFrame(
            {"roe": [0.1, 0.2]},
            index=pd.MultiIndex.from_product(
                [fin_dates, ["000001"]], names=["date", "symbol"]
            ),
        )

        provider = _make_ffill_provider(kind, price_df, fin_df)
        result = provider.get_merged_panel(
            symbols=["000001"],
            start_date="2023-04-01",
            end_date="2023-06-30",
        )

        sym = result.xs("000001", level="symbol")
        # 4-01: 无行情，close 必须保持 NaN（禁止价格 ffill）
        assert pd.isna(sym.loc[pd.Timestamp("2023-04-01"), "close"]), (
            f"{kind}: 价格列被错误 ffill，4-01 close 应为 NaN"
        )
        assert sym.loc[pd.Timestamp("2023-04-01"), "roe"] == 0.1
        # 5-01: 有行情；ROE 由 4-01 前向填充
        assert sym.loc[pd.Timestamp("2023-05-01"), "close"] == 11.0
        assert sym.loc[pd.Timestamp("2023-05-01"), "roe"] == 0.1
        # 6-01: 行情与财务均有值
        assert sym.loc[pd.Timestamp("2023-06-01"), "close"] == 12.0
        assert sym.loc[pd.Timestamp("2023-06-01"), "roe"] == 0.2


# ── AUDIT-P2-07: 复权一致性 ─────────────────────────────────────


@_PG_SKIP
def test_adjustment_consistency_no_false_positive():
    """正常复权数据不应产生可疑跳跃（AUDIT-P2-07）。"""
    import uuid

    import pandas as pd

    from long_earn.backtest.data.cache import DataCache

    symbol = f"ADJN-{uuid.uuid4().hex[:10]}"
    cache = DataCache()
    try:
        # 构造正常数据：每日上涨 1%，无异常跳跃
        dates = pd.date_range("2024-01-01", periods=100, freq="B")
        closes = [100.0 * (1.01**i) for i in range(len(dates))]
        df = pd.DataFrame(
            {
                "symbol": [symbol] * len(dates),
                "date": dates,
                "open": closes,
                "high": [c * 1.01 for c in closes],
                "low": [c * 0.99 for c in closes],
                "close": closes,
                "volume": 10000.0,
            }
        )
        cache.save_prices(df)

        suspicious = cache.check_adjustment_consistency([symbol])
        assert len(suspicious) == 0, (
            f"正常数据不应产生可疑跳跃，实际 {len(suspicious)} 条"
        )

        cache.close()
    finally:
        # 清理测试数据（避免污染 PG 共享库）
        cache = DataCache()
        try:
            cache._get_conn().execute(
                "DELETE FROM price_daily WHERE symbol = %s", [symbol]
            )
            cache._get_conn().commit()
        finally:
            cache.close()


@_PG_SKIP
def test_adjustment_consistency_detects_jump():
    """复权异常（单日暴跌 60%）应被检测到（AUDIT-P2-07）。"""
    import uuid

    import pandas as pd

    from long_earn.backtest.data.cache import DataCache

    symbol = f"ADJJ-{uuid.uuid4().hex[:10]}"
    cache = DataCache()
    try:
        dates = pd.date_range("2024-01-01", periods=5, freq="B")
        closes = [100.0, 101.0, 40.0, 41.0, 42.0]  # 第 3 天暴跌 60%
        df = pd.DataFrame(
            {
                "symbol": [symbol] * len(dates),
                "date": dates,
                "open": closes,
                "high": [c * 1.01 for c in closes],
                "low": [c * 0.99 for c in closes],
                "close": closes,
                "volume": 10000.0,
            }
        )
        cache.save_prices(df)

        suspicious = cache.check_adjustment_consistency([symbol])
        assert len(suspicious) == 1, f"应检测到 1 条可疑跳跃，实际 {len(suspicious)} 条"
        assert suspicious[0]["symbol"] == symbol
        assert suspicious[0]["return_pct"] < -50, (
            f"收益率应为 -60% 左右，实际 {suspicious[0]['return_pct']}%"
        )

        cache.close()
    finally:
        # 清理测试数据（避免污染 PG 共享库）
        cache = DataCache()
        try:
            cache._get_conn().execute(
                "DELETE FROM price_daily WHERE symbol = %s", [symbol]
            )
            cache._get_conn().commit()
        finally:
            cache.close()


# ── 委托契约：ciccwm/akshare 的 get_merged_panel（无 ffill） ──────────


class TestMergedPanelDelegationContract:
    """ciccwm/akshare 的 get_merged_panel 委托 get_price_panel（无 merge+ffill）。

    ADR-007 Phase 3 财务数据统一到 miniqmt 后，这两个 provider 的
    get_merged_panel 仅返回行情面板，不执行 groupby.ffill，因此
    ffill 排序契约对它们 N/A。此处验证委托契约：get_merged_panel
    返回 get_price_panel 的结果，且索引已排序。
    """

    @pytest.mark.parametrize("kind", ["ciccwm", "akshare"])
    def test_merged_panel_delegates_to_price_panel(self, kind: str):
        """get_merged_panel 应返回 get_price_panel 的结果（无 ffill，直接委托）。"""
        price_dates = pd.to_datetime(["2023-04-01", "2023-05-01", "2023-06-01"])
        price_df = pd.DataFrame(
            {"close": [10.0, 11.0, 12.0]},
            index=pd.MultiIndex.from_product(
                [price_dates, ["000001"]], names=["date", "symbol"]
            ),
        )

        if kind == "ciccwm":
            provider: Any = CiccwmDataProvider.__new__(CiccwmDataProvider)
        else:
            from long_earn.backtest.data.akshare_provider import (
                AkshareFallbackProvider,
            )

            provider = AkshareFallbackProvider.__new__(AkshareFallbackProvider)

        provider.get_price_panel = (  # type: ignore[method-assign]
            lambda *_a, **_k: price_df
        )

        result = provider.get_merged_panel(
            symbols=["000001"],
            start_date="2023-04-01",
            end_date="2023-06-30",
        )

        # 委托契约：返回的就是 get_price_panel 的结果
        pd.testing.assert_frame_equal(result, price_df)
        # 索引已排序（由 get_price_panel 保证）
        assert result.index.is_monotonic_increasing
