"""PIT 修复回归测试（防回退）。

将 scripts/test_pit_fix_e2e.py 的 PIT 验证核心逻辑转为 pytest 单元测试。
原脚本依赖真实记忆系统 + akshare/miniqmt + 完整回测链路，通过回测 metrics
（factor_failures=0 / metrics_unreliable=False）间接验证 PIT 修复。

本测试用 DuckDB 临时缓存 + 注入 MiniQmtDataProvider（强制 miniqmt 不可用）
走完整 get_financial_panel 链路，直接验证 announce_date PIT 对齐——
这是 PIT 修复生效的直接证据，比回测 metrics 间接判定更可靠。

核心契约（ADR-007 Phase 1）：
  - timestamp=T 的日频面板行只含 announce_date <= T 的报告值
  - 公告日前字段为 NaN（杜绝未来函数）
  - 公告日当天起字段可见
  - 第二份报告公告后覆盖第一份
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from long_earn.backtest.data.miniqmt_provider import MiniQmtDataProvider

# ── 测试数据构造 ─────────────────────────────────────────────


def _make_pit_fixture_data() -> pd.DataFrame:
    """构造 PIT 回归测试的季频财务数据（多报告期 + 多 symbol）。

    关键场景（对应 scripts/test_pit_fix_e2e.py 验证的 PIT 修复点）：
      - 600519.SH: 2020-Q1 announce_date=2020-04-28（周二）, revenue=100
                   2020-Q2 announce_date=2020-08-20（周四）, revenue=200
      - 000001.SZ: 2020-Q1 announce_date=2020-04-29（周三）, revenue=50

    覆盖公告日前后、报告覆盖、多 symbol 隔离三个 PIT 维度。
    """
    return pd.DataFrame(
        {
            "symbol": ["600519.SH", "600519.SH", "000001.SZ"],
            "report_date": pd.to_datetime(
                ["2020-03-31", "2020-06-30", "2020-03-31"]
            ),
            "announce_date": pd.to_datetime(
                ["2020-04-28", "2020-08-20", "2020-04-29"]
            ),
            "revenue": [100.0, 200.0, 50.0],
            "net_profit": [10.0, 20.0, 5.0],
            "eps": [0.8, 1.6, 0.4],
        }
    )


@pytest.fixture
def pit_provider(tmp_path: Any) -> MiniQmtDataProvider:
    """构造一个不依赖 xtquant 的 MiniQmtDataProvider（mock 数据源）。

    - 用临时 DuckDB 缓存隔离测试
    - 预填 PIT 测试财务数据
    - 强制 client.is_available=False，避免触发真实 miniqmt 网络调用
      （等价于 CI/无 QMT 环境，走纯 cache 路径）
    """
    from long_earn.backtest.data.cache import DataCache

    cache = DataCache(db_path=tmp_path / "pit_regression.duckdb")
    cache.save_financials(_make_pit_fixture_data())
    provider = MiniQmtDataProvider(cache=cache)
    # 强制 xtquant 不可用，走纯 DuckDB 缓存路径
    provider.client._available = False
    return provider


# ── PIT 回归测试（防回退） ───────────────────────────────────


@pytest.mark.regression
class TestPITRegression:
    """PIT 修复回归测试：验证完整 get_financial_panel 链路无未来函数。

    对应 scripts/test_pit_fix_e2e.py 的 _verdict 判定：
      - factor_failures=0 → 策略不在 NaN 上求值
        （本测试直接验证公告日前字段为 NaN）
      - metrics_unreliable=False → PIT 修复生效
        （本测试直接验证日频面板无未来函数）
    """

    def test_no_future_function_before_announce_date(
        self, pit_provider: MiniQmtDataProvider
    ) -> None:
        """公告日前字段必须为 NaN——PIT 契约核心（杜绝未来函数）。

        场景：600519.SH 的 2020-Q1 报告 announce_date=2020-04-28，
        在 2020-04-01~04-27 的日频面板上 revenue 必须为 NaN。
        """
        result = pit_provider.get_financial_panel(
            ["600519.SH"], "2020-04-01", "2020-04-27", fields=["revenue"]
        )
        assert not result.empty
        assert result["revenue"].isna().all(), (
            "PIT 违规：announce_date(2020-04-28) 之前的日频面板"
            "返回了非 NaN revenue，存在未来函数泄漏"
        )

    def test_visible_after_announce_date(
        self, pit_provider: MiniQmtDataProvider
    ) -> None:
        """公告日后字段可见——announce_date 对齐生效。

        场景：600519.SH 的 2020-Q1 报告 announce_date=2020-04-28，
        在 2020-04-28~06-30 的日频面板上 revenue 必须为 100。
        """
        result = pit_provider.get_financial_panel(
            ["600519.SH"], "2020-04-28", "2020-06-30", fields=["revenue"]
        )
        assert not result.empty
        visible = result[result["revenue"].notna()]
        assert not visible.empty
        assert (visible["revenue"] == 100.0).all(), (
            f"公告日后应返回第一份报告 revenue=100，"
            f"实际={visible['revenue'].tolist()}"
        )

    def test_second_report_overrides_after_its_announce_date(
        self, pit_provider: MiniQmtDataProvider
    ) -> None:
        """第二份报告公告后覆盖第一份——announce_date 后看到新值。

        场景：600519.SH 的 2020-Q2 报告 announce_date=2020-08-20，
        在 2020-09 月的日频面板上 revenue 必须为 200（第二份覆盖第一份）。
        """
        result = pit_provider.get_financial_panel(
            ["600519.SH"], "2020-09-01", "2020-09-15", fields=["revenue"]
        )
        assert not result.empty
        visible = result[result["revenue"].notna()]
        assert not visible.empty
        assert (visible["revenue"] == 200.0).all(), (
            f"第二份报告公告后应返回 revenue=200，"
            f"实际={visible['revenue'].tolist()}"
        )

    def test_announce_date_boundary_exact(
        self, pit_provider: MiniQmtDataProvider
    ) -> None:
        """公告日当天边界精确性——当天即可见，前一天为 NaN。

        场景：600519.SH announce_date=2020-04-28（周二）。
          - 2020-04-27（周一，公告日前最后交易日）→ NaN
          - 2020-04-28（周二，公告日当天）→ 100
          - 2020-04-29（周三，公告日后）→ 100
        """
        result = pit_provider.get_financial_panel(
            ["600519.SH"], "2020-04-27", "2020-04-29", fields=["revenue"]
        )
        assert not result.empty
        flat = result.reset_index().sort_values("date")
        vals_by_date = dict(
            zip(
                flat["date"].dt.strftime("%Y-%m-%d"),
                flat["revenue"],
                strict=True,
            )
        )
        assert pd.isna(vals_by_date["2020-04-27"]), (
            f"公告日前一天（2020-04-27）应 NaN，"
            f"实际={vals_by_date['2020-04-27']}"
        )
        assert vals_by_date["2020-04-28"] == 100.0, (
            f"公告日当天（2020-04-28）应可见 revenue=100，"
            f"实际={vals_by_date['2020-04-28']}"
        )
        assert vals_by_date["2020-04-29"] == 100.0, (
            f"公告日后一天（2020-04-29）应可见 revenue=100，"
            f"实际={vals_by_date['2020-04-29']}"
        )

    def test_multi_symbol_pit_isolation(
        self, pit_provider: MiniQmtDataProvider
    ) -> None:
        """多 symbol 场景 PIT 隔离——每只股票按各自 announce_date 对齐。

        场景：2020-04-28 当天的日频面板：
          - 600519.SH announce_date=2020-04-28 → 已公告 → revenue=100
          - 000001.SZ announce_date=2020-04-29 → 未公告 → revenue=NaN
        """
        result = pit_provider.get_financial_panel(
            ["600519.SH", "000001.SZ"],
            "2020-04-28",
            "2020-04-28",
            fields=["revenue"],
        )
        assert not result.empty
        flat = result.reset_index()
        moutai = flat[flat["symbol"] == "600519.SH"]
        pingan = flat[flat["symbol"] == "000001.SZ"]
        assert not moutai.empty, "600519.SH 应有数据行"
        assert not pingan.empty, "000001.SZ 应有数据行"
        assert moutai["revenue"].iloc[0] == 100.0, (
            "600519.SH 在 2020-04-28（公告日当天）应可见 revenue=100"
        )
        assert pd.isna(pingan["revenue"].iloc[0]), (
            "000001.SZ 在 2020-04-28（公告日 04-29 之前）应为 NaN"
        )
