"""牛熊门控（regime gate）单元测试：DSL 哑铃策略核心链路。

覆盖点（按项目规范：接口契约 + 系统关键环节）：
1. DSL 解析：regime 字段可选、缺 benchmark 拒绝、warmup 含均线窗口
2. 端到端：牛市走算子选股、熊市切换防守腿 ETF（切换日强制调仓）
3. 退化路径：benchmark 行缺失时记诊断并退化为始终牛市
"""

from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import pytest

from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.dsl import compute_warmup_days, parse_strategy_yaml
from long_earn.backtest.engine.dsl_strategy import DSLStrategy

REGIME_YAML = """
strategy:
  name: RegimeBarbell
  description: 牛熊门控哑铃策略
  universe: { type: csi300, rebalance_freq: 20D }
  operator_factors:
    - op: returns
      alias: mom
      params: { field: close, period: 5 }
  signals:
    - type: operator
      op: filter_threshold
      params: { field: mom, op: ">", value: 0.0 }
    - type: operator
      op: rank_top
      params: { field: mom, top: 2, ascending: false }
  weights: { method: equal }
  regime:
    benchmark: IDX.SH
    window: 10
    defensive_assets: ["DEF.SH"]
"""

REL_YAML = """
strategy:
  name: RelBarbell
  description: 池相对强度门控哑铃策略
  universe: { type: csi300, rebalance_freq: 20D }
  operator_factors:
    - op: returns
      alias: mom
      params: { field: close, period: 5 }
  signals:
    - type: operator
      op: filter_threshold
      params: { field: mom, op: ">", value: 0.0 }
    - type: operator
      op: rank_top
      params: { field: mom, top: 2, ascending: false }
  weights: { method: equal }
  regime:
    benchmark: IDX.SH
    window: 10
    mode: relative
    rel_window: 5
    rel_margin: 0.0
    defensive_assets: ["DEF.SH"]
"""


def _style_crash_panel() -> pl.DataFrame:
    """风格崩盘测试面板（2025Q1 复刻）：指数横盘，池内股票崩盘。

    IDX 恒定 100（绝对均线模式判牛），A/B 每日 -1%（池动量崩），
    DEF 恒定（防守腿）。rel_window=5 下第 6 日起池动量约 -5% vs 指数 0%
    → relative 门判熊。
    """
    rows = []
    base = datetime(2024, 1, 1)
    for i in range(40):
        ts = base + timedelta(days=i)
        for sym, close in [
            ("A.SZ", round(10.0 * 0.99**i, 4)),
            ("B.SH", round(10.0 * 0.99**i, 4)),
            ("DEF.SH", 50.0),
            ("IDX.SH", 100.0),
        ]:
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": sym,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 10000.0,
                }
            )
    return pl.DataFrame(rows)


def _pool_outperform_panel() -> pl.DataFrame:
    """池跑赢面板：A/B 每日 +0.5%，IDX 恒定（相对门判牛，全程持股票腿）。"""
    rows = []
    base = datetime(2024, 1, 1)
    for i in range(40):
        ts = base + timedelta(days=i)
        for sym, close in [
            ("A.SZ", round(10.0 * 1.005**i, 4)),
            ("B.SH", round(10.0 * 1.005**i, 4)),
            ("DEF.SH", 50.0),
            ("IDX.SH", 100.0),
        ]:
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": sym,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 10000.0,
                }
            )
    return pl.DataFrame(rows)


def _index_bull_pool_crash_panel() -> pl.DataFrame:
    """判别性面板（隔离 combined 的 rel 分支）：指数上行（绝对门判牛），
    池内股票崩盘（相对门判熊）。combined 模式下仅 rel 分支能触发熊市。
    """
    rows = []
    base = datetime(2024, 1, 1)
    for i in range(40):
        ts = base + timedelta(days=i)
        for sym, close in [
            ("A.SZ", round(10.0 * 0.99**i, 4)),
            ("B.SH", round(10.0 * 0.99**i, 4)),
            ("DEF.SH", 50.0),
            ("IDX.SH", round(100.0 * 1.002**i, 4)),
        ]:
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": sym,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 10000.0,
                }
            )
    return pl.DataFrame(rows)


def _barbell_panel(with_benchmark: bool = True) -> pl.DataFrame:
    """哑铃测试面板：A/B 上行（牛市腿），IDX 前 20 天涨后 20 天跌（牛→熊），
    DEF 恒定（防守腿）。

    IDX 时间线（window=10 均线）：day0-19 上行 → bull；day21+ 跌破均线 → bear。
    rebalance_freq=20D 下若切换日不强制调仓，防守腿永远不会被买入。
    """
    rows = []
    base = datetime(2024, 1, 1)
    for i in range(40):
        ts = base + timedelta(days=i)
        for sym, close in [
            ("A.SZ", round(10.0 * 1.005**i, 4)),
            ("B.SH", round(10.0 * 1.003**i, 4)),
            ("DEF.SH", 50.0),
        ]:
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": sym,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 10000.0,
                }
            )
        if with_benchmark:
            idx_close = (
                100.0 * 1.01**i if i < 20 else 122.0 * 0.97 ** (i - 20)
            )
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": "IDX.SH",
                    "open": idx_close,
                    "high": idx_close * 1.01,
                    "low": idx_close * 0.99,
                    "close": round(idx_close, 4),
                    "volume": 100000.0,
                }
            )
    return pl.DataFrame(rows)


def _fill_symbols(engine: EventDrivenBacktestEngine) -> set[str]:
    """从引擎内存审计 trail 提取 FILL 事件的成交标的集合。"""
    symbols = set()
    for event in engine.audit_logger.get_full_trail():
        if event.get("event_type") != "FILL":
            continue
        payload = event.get("payload") or {}
        sym = payload.get("symbol")
        if sym:
            symbols.add(sym)
    return symbols


class TestRegimeDslParse:
    def test_regime_optional(self):
        """无 regime 字段的 DSL 解析正常且 regime 为 None。"""
        yaml_no_regime = REGIME_YAML.split("  regime:", maxsplit=1)[0] + "  weights: { method: equal }\n"
        dsl = parse_strategy_yaml(yaml_no_regime)
        assert dsl.regime is None

    def test_regime_requires_benchmark(self):
        """regime 配置缺 benchmark 必须解析期拒绝。"""
        bad = REGIME_YAML.replace("benchmark: IDX.SH\n", "")
        with pytest.raises(ValueError):
            parse_strategy_yaml(bad)

    def test_warmup_includes_regime_window(self):
        """warmup 必须覆盖 regime 均线窗口（否则前 N 天门控盲区）。"""
        dsl = parse_strategy_yaml(REGIME_YAML)
        assert compute_warmup_days(dsl) >= 10 * 1.5 + 30
        long_dsl = parse_strategy_yaml(REGIME_YAML.replace("window: 10", "window: 250"))
        assert compute_warmup_days(long_dsl) >= 250 * 1.5 + 30

    def test_regime_spec_probe(self):
        """regime_spec 探针暴露配置（引擎据此并入预取标的）。"""
        dsl = parse_strategy_yaml(REGIME_YAML)
        strategy = DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl)
        assert strategy.regime_spec is not None
        assert set(strategy.regime_spec.non_pool_symbols()) == {"IDX.SH", "DEF.SH"}


class TestRegimeGateE2E:
    def test_bull_picks_stocks_bear_switches_to_defensive(self, mock_data_provider):
        """端到端：牛市买股票腿，熊市切换防守腿 ETF。

        DEF.SH 的成交同时证明两件事：熊市门控生效 + 切换日强制调仓生效
        （bear 出现在 day21+，非 rebalance_freq=20D 的调仓相位）。
        """
        dsl = parse_strategy_yaml(REGIME_YAML)
        engine = EventDrivenBacktestEngine(
            data_provider=mock_data_provider(_barbell_panel())
        )
        strategy = DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl)
        result = engine.run(
            strategy,
            "2024-01-01",
            "2024-02-09",
            ["A.SZ", "B.SH", "DEF.SH", "IDX.SH"],
        )
        assert result.success, result.message
        filled = _fill_symbols(engine)
        assert "DEF.SH" in filled, f"熊市应买入防守腿, 实际成交: {filled}"
        assert filled & {"A.SZ", "B.SH"}, f"牛市应买入股票腿, 实际成交: {filled}"

    def test_benchmark_missing_degrades_to_bull(self, mock_data_provider):
        """benchmark 行缺失：记一次诊断，门控退化为始终牛市（不阻断回测）。"""
        dsl = parse_strategy_yaml(REGIME_YAML)
        engine = EventDrivenBacktestEngine(
            data_provider=mock_data_provider(_barbell_panel(with_benchmark=False))
        )
        strategy = DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl)
        result = engine.run(
            strategy,
            "2024-01-01",
            "2024-02-09",
            ["A.SZ", "B.SH", "DEF.SH"],
        )
        assert result.success, result.message
        missing = [
            f for f in strategy.step_failures if f["type"] == "regime_benchmark_missing"
        ]
        assert len(missing) == 1, "benchmark 缺失诊断应恰好记一次"


class TestRelativeRegime:
    def test_mode_validation(self):
        """非法 mode 必须解析期拒绝。"""
        with pytest.raises(ValueError):
            parse_strategy_yaml(REL_YAML.replace("mode: relative", "mode: bogus"))

    def test_warmup_includes_rel_window(self):
        """relative/combined 模式 warmup 必须覆盖 rel_window。"""
        dsl = parse_strategy_yaml(REL_YAML.replace("rel_window: 5", "rel_window: 250"))
        assert compute_warmup_days(dsl) >= 250 * 1.5 + 30
        combined = parse_strategy_yaml(
            REL_YAML.replace("mode: relative", "mode: combined").replace(
                "rel_window: 5", "rel_window: 250"
            )
        )
        assert compute_warmup_days(combined) >= 250 * 1.5 + 30

    def test_style_crash_switches_to_defensive(self, mock_data_provider):
        """2025Q1 复刻：指数横盘（绝对门判牛），池崩盘 → relative 门判熊切防守腿。

        这是 OOS fold 0 失败场景的直接回归测试：absolute 模式在该面板下
        全程满仓股票腿挨打，relative 模式必须切换。
        """
        dsl = parse_strategy_yaml(REL_YAML)
        engine = EventDrivenBacktestEngine(
            data_provider=mock_data_provider(_style_crash_panel())
        )
        strategy = DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl)
        result = engine.run(
            strategy,
            "2024-01-01",
            "2024-02-09",
            ["A.SZ", "B.SH", "DEF.SH", "IDX.SH"],
        )
        assert result.success, result.message
        filled = _fill_symbols(engine)
        assert "DEF.SH" in filled, f"风格崩盘应切换防守腿, 实际成交: {filled}"

    def test_pool_outperform_stays_in_stocks(self, mock_data_provider):
        """池跑赢指数 → 全程牛市持股票腿，防守腿零成交。"""
        dsl = parse_strategy_yaml(REL_YAML)
        engine = EventDrivenBacktestEngine(
            data_provider=mock_data_provider(_pool_outperform_panel())
        )
        strategy = DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl)
        result = engine.run(
            strategy,
            "2024-01-01",
            "2024-02-09",
            ["A.SZ", "B.SH", "DEF.SH", "IDX.SH"],
        )
        assert result.success, result.message
        filled = _fill_symbols(engine)
        assert "DEF.SH" not in filled, f"牛市不应触碰防守腿, 实际成交: {filled}"
        assert filled & {"A.SZ", "B.SH"}, f"牛市应买入股票腿, 实际成交: {filled}"

    def test_rel_margin_suppresses_noise(self, mock_data_provider):
        """margin 校准：池轻微落后（-5%）在 margin=10% 内不触发熊市。"""
        yaml_wide = REL_YAML.replace("rel_margin: 0.0", "rel_margin: 0.1")
        dsl = parse_strategy_yaml(yaml_wide)
        engine = EventDrivenBacktestEngine(
            data_provider=mock_data_provider(_style_crash_panel())
        )
        strategy = DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl)
        result = engine.run(
            strategy,
            "2024-01-01",
            "2024-02-09",
            ["A.SZ", "B.SH", "DEF.SH", "IDX.SH"],
        )
        assert result.success, result.message
        filled = _fill_symbols(engine)
        assert "DEF.SH" not in filled, (
            f"落后未超 margin 不应切防守腿, 实际成交: {filled}"
        )


class TestCombinedRegime:
    def test_index_bull_pool_crash_fires_via_rel_branch(self, mock_data_provider):
        """combined 判别性测试：指数上行（abs=牛）+ 池崩盘（rel=熊）→ 切防守腿。

        面板专门构造为只有 rel 分支能触发，证明 combined 的 OR 逻辑接入了
        相对强度信号（而非仅绝对均线）。
        """
        yaml_combined = REL_YAML.replace("mode: relative", "mode: combined")
        dsl = parse_strategy_yaml(yaml_combined)
        engine = EventDrivenBacktestEngine(
            data_provider=mock_data_provider(_index_bull_pool_crash_panel())
        )
        strategy = DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl)
        result = engine.run(
            strategy,
            "2024-01-01",
            "2024-02-09",
            ["A.SZ", "B.SH", "DEF.SH", "IDX.SH"],
        )
        assert result.success, result.message
        filled = _fill_symbols(engine)
        assert "DEF.SH" in filled, f"rel 分支应触发熊市切防守腿, 实际成交: {filled}"

    def test_market_crash_fires_via_abs_branch(self, mock_data_provider):
        """combined 市场级崩盘：指数跌破均线（abs=熊）→ 切防守腿（经典路径保持）。"""
        yaml_combined = REL_YAML.replace("mode: relative", "mode: combined")
        dsl = parse_strategy_yaml(yaml_combined)
        engine = EventDrivenBacktestEngine(
            data_provider=mock_data_provider(_barbell_panel())
        )
        strategy = DSLStrategy(strategy_id=dsl.name, dsl_strategy=dsl)
        result = engine.run(
            strategy,
            "2024-01-01",
            "2024-02-09",
            ["A.SZ", "B.SH", "DEF.SH", "IDX.SH"],
        )
        assert result.success, result.message
        filled = _fill_symbols(engine)
        assert "DEF.SH" in filled, f"abs 分支应触发熊市切防守腿, 实际成交: {filled}"
