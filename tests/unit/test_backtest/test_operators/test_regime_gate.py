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
