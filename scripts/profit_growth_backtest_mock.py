"""利润增长策略回测脚本 - 使用模拟数据。

用于验证回测引擎和策略逻辑，无需连接外部数据源。

用法:
    python scripts/profit_growth_backtest_mock.py
"""

from __future__ import annotations

import logging
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import polars as pl

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.dsl import TradingCostConfig
from long_earn.backtest.engine.strategy import BaseStrategy

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def generate_mock_data(
    symbols: list[str],
    start_date: str = "2020-01-01",
    end_date: str = "2023-12-31",
) -> pl.DataFrame:
    """生成模拟股票数据用于回测。"""
    dates = pd.date_range(start=start_date, end=end_date, freq="B")
    rows = []
    base_prices = {s: random.uniform(10, 100) for s in symbols}
    current_prices = base_prices.copy()

    for date in dates:
        for symbol in symbols:
            # 模拟价格波动
            change = random.gauss(0.0005, 0.02)
            current_prices[symbol] *= 1 + change
            close = current_prices[symbol]
            open_ = close * (1 + random.uniform(-0.01, 0.01))
            high = max(open_, close) * (1 + random.uniform(0, 0.02))
            low = min(open_, close) * (1 - random.uniform(0, 0.02))
            volume = random.randint(1000000, 50000000)

            # 模拟财务数据（大部分股票满足利润增长条件）
            net_profit_yoy = random.uniform(-0.1, 0.5) if random.random() > 0.2 else random.uniform(-0.5, -0.1)
            roe = random.uniform(0.05, 0.20)
            revenue_yoy = random.uniform(-0.1, 0.3)

            rows.append({
                "timestamp": pd.Timestamp(date),
                "symbol": symbol,
                "open": round(open_, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "volume": volume,
                "net_profit_yoy": round(net_profit_yoy, 4),
                "roe": round(roe, 4),
                "revenue_yoy": round(revenue_yoy, 4),
            })

    df = pl.from_pandas(pd.DataFrame(rows))
    return df.sort(["symbol", "timestamp"])


class MockDataProvider:
    """模拟数据提供者。"""

    def __init__(self, data: pl.DataFrame):
        self._data = data

    def get_merged_panel_as_polars(self, symbols: list[str], start: str, end: str) -> pl.DataFrame:
        df = self._data.filter(
            pl.col("symbol").is_in(symbols),
            pl.col("timestamp") >= pd.Timestamp(start),
            pl.col("timestamp") <= pd.Timestamp(end),
        )
        return df


class ProfitGrowthDSLStrategy(BaseStrategy):
    """利润增长 DSL 策略。"""

    def __init__(self, strategy_id: str, config: dict | None = None):
        super().__init__(strategy_id, config)
        self.max_holding = config.get("max_holding", 20) if config else 20
        self.min_profit_yoy = config.get("min_profit_yoy", 0.2) if config else 0.2
        self.min_roe = config.get("min_roe", 0.08) if config else 0.08

    def on_bar(self, bars: pl.DataFrame, context) -> any:
        from long_earn.backtest.domain.entities import SignalEvent

        # 过滤满足条件的股票
        candidates = bars.filter(
            pl.col("net_profit_yoy") > self.min_profit_yoy,
            pl.col("roe") > self.min_roe,
            pl.col("revenue_yoy") > 0,
        )

        if candidates.is_empty():
            return None

        # 按净利润增长率排序
        top = candidates.sort("net_profit_yoy", descending=True).head(self.max_holding)
        symbols = top["symbol"].to_list()

        # 等权分配
        weights = {s: 1.0 / len(symbols) for s in symbols}

        return SignalEvent(
            timestamp=context.current_timestamp,
            trace_id=f"profit_growth_{context.current_timestamp.isoformat()}",
            event_id=f"signal_{context.current_timestamp.isoformat()}",
            signals=weights,
            strategy_id=self.strategy_id,
        )


def run_backtest(
    name: str,
    config: dict,
    start_date: str,
    end_date: str,
    symbols: list[str],
) -> dict:
    """运行回测。"""
    logger.info(f"\n{'='*50}")
    logger.info(f"回测: {name}")
    logger.info(f"{'='*50}")

    # 生成模拟数据
    data = generate_mock_data(symbols, start_date, end_date)
    logger.info(f"生成 {len(data)} 条模拟数据, {len(symbols)} 只股票")

    # 创建引擎
    engine = EventDrivenBacktestEngine(
        cost_config=TradingCostConfig(
            commission_rate=0.0003,
            stamp_duty=0.0005,
            slippage_bps=2.0,
        ),
        stop_loss=config.get("stop_loss", 0.15),
        max_drawdown_limit=config.get("max_drawdown", 0.25),
        max_position_pct=config.get("max_position", 0.1),
    )
    engine.data_provider = MockDataProvider(data)

    # 创建策略
    strategy = ProfitGrowthDSLStrategy(
        strategy_id=name,
        config=config,
    )

    # 运行回测
    result = engine.run(strategy, start_date, end_date, symbols)

    if result.success:
        metrics = {
            "name": name,
            "total_return": result.total_return,
            "annual_return": result.annual_return,
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown": result.max_drawdown,
            "win_rate": result.win_rate,
            "trading_days": result.trading_days,
            "volatility": result.volatility,
            "calmar_ratio": result.calmar_ratio,
            "sortino_ratio": result.sortino_ratio,
        }
        return metrics
    else:
        return {"name": name, "error": result.message}


def print_report(report: dict):
    """打印回测报告。"""
    if "error" in report:
        print(f"\n回测失败: {report['error']}")
        return

    print("\n" + "=" * 60)
    print(f"  策略: {report['name']}")
    print("=" * 60)
    print(f"  总收益率:       {report.get('total_return', 0) * 100:.2f}%")
    print(f"  年化收益率:     {report.get('annual_return', 0) * 100:.2f}%")
    print(f"  最大回撤:       {report.get('max_drawdown', 0) * 100:.2f}%")
    print(f"  夏普比率:       {report.get('sharpe_ratio', 0):.2f}")
    print(f"  索提诺比率:     {report.get('sortino_ratio', 0):.2f}")
    print(f"  卡玛比率:       {report.get('calmar_ratio', 0):.2f}")
    print(f"  胜率:           {report.get('win_rate', 0) * 100:.2f}%")
    print(f"  波动率:         {report.get('volatility', 0) * 100:.2f}%")
    print(f"  交易天数:       {report.get('trading_days', 0)}")
    print("=" * 60 + "\n")


def main():
    """主函数。"""
    logger.info("利润增长策略回测（模拟数据版）")
    logger.info("=" * 50)

    # 模拟股票池
    symbols = [f"{i:06d}.SZ" for i in range(1, 51)]  # 50只模拟股票
    start_date = "2020-01-01"
    end_date = "2023-12-31"

    # 不同参数配置
    configs = [
        {
            "name": "保守型",
            "config": {"min_profit_yoy": 0.15, "min_roe": 0.06, "max_holding": 15, "stop_loss": 0.12, "max_drawdown": 0.20},
        },
        {
            "name": "平衡型",
            "config": {"min_profit_yoy": 0.20, "min_roe": 0.08, "max_holding": 20, "stop_loss": 0.15, "max_drawdown": 0.25},
        },
        {
            "name": "进取型",
            "config": {"min_profit_yoy": 0.30, "min_roe": 0.10, "max_holding": 10, "stop_loss": 0.20, "max_drawdown": 0.30},
        },
    ]

    results = []
    for cfg in configs:
        report = run_backtest(cfg["name"], cfg["config"], start_date, end_date, symbols)
        results.append(report)
        print_report(report)

    # 对比结果
    print("\n" + "=" * 70)
    print("  参数优化结果对比")
    print("=" * 70)
    print(f"{'策略':<10} {'年化收益':>10} {'最大回撤':>10} {'夏普比率':>10} {'索提诺':>10}")
    print("-" * 70)

    for r in results:
        if "error" not in r:
            print(
                f"{r['name']:<10} "
                f"{r['annual_return']*100:>9.2f}% "
                f"{r['max_drawdown']*100:>9.2f}% "
                f"{r['sharpe_ratio']:>10.2f} "
                f"{r['sortino_ratio']:>10.2f}"
            )

    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
