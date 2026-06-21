#!/usr/bin/env python3
"""利润增长策略回测脚本 - 使用 long-earn agent 模块。

通过 long-earn 的 agent 模块和 backtest 服务，测试利润增长策略的可行性。

用法:
    uv run python scripts/profit_growth_backtest.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from long_earn.config import AppConfig
from long_earn.context_init import create_runtime_context
from long_earn.services.backtest_service import BacktestServiceImpl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# 利润增长策略 YAML 定义
PROFIT_GROWTH_STRATEGY = """
name: ProfitGrowthStrategy
description: |
  利润增长策略 - 选择净利润连续增长、ROE 优良的股票
  选股逻辑：
  1. 净利润同比增长率 > 20%
  2. ROE > 8%
  3. 营业收入同比增长 > 0%
  4. 按净利润增长率排序，取前 20 只

universe:
  type: csi300
  rebalance_freq: 20D

factors:
  profit_growth_score: "net_profit_yoy * 0.3 + roe * 0.25 + revenue_yoy * 0.15"

signals:
  - type: filter
    condition: "net_profit_yoy > 0.20"
  - type: filter
    condition: "roe > 0.08"
  - type: filter
    condition: "revenue_yoy > 0"
  - type: rank
    by: profit_growth_score
    ascending: false
    top: 20

weights:
  method: equal

risk_control:
  max_position_per_stock: 0.1
  stop_loss: 0.15
  max_drawdown_limit: 0.25

trading_cost:
  commission_rate: 0.0003
  stamp_duty: 0.0005
  slippage_bps: 2.0
"""


def print_report(report: dict):
    """打印回测报告。"""
    if "error" in report:
        print(f"\n回测失败: {report['error']}")
        if "error_detail" in report:
            print(f"详情: {report['error_detail']}")
        return

    print("\n" + "=" * 60)
    print("  利润增长策略回测报告")
    print("=" * 60)

    print(f"\n  总收益率:       {report.get('total_return', 0) * 100:.2f}%")
    print(f"  年化收益率:     {report.get('annual_return', 0) * 100:.2f}%")
    print(f"  最大回撤:       {report.get('max_drawdown', 0) * 100:.2f}%")
    print(f"  夏普比率:       {report.get('sharpe_ratio', 0):.2f}")
    print(f"  胜率:           {report.get('win_rate', 0) * 100:.2f}%")
    print(f"  波动率:         {report.get('volatility', 0) * 100:.2f}%")

    if "calmar_ratio" in report:
        print(f"  卡玛比率:       {report.get('calmar_ratio', 0):.2f}")
    if "sortino_ratio" in report:
        print(f"  索提诺比率:     {report.get('sortino_ratio', 0):.2f}")

    print(f"\n  交易天数:       {report.get('trading_days', 0)}")
    print("=" * 60 + "\n")


def run_backtest(
    strategy_yaml: str,
    start_date: str = "2020-01-01",
    end_date: str = "2023-12-31",
):
    """运行回测。"""
    # 创建配置和上下文
    config = AppConfig.from_env()
    config.backtest_start_date = start_date
    config.backtest_end_date = end_date

    context = create_runtime_context(config)
    backtest_service = BacktestServiceImpl(context)

    # 执行回测
    logger.info(f"开始回测: {start_date} ~ {end_date}")
    report = backtest_service.run(
        strategy_yaml=strategy_yaml,
        start_date=start_date,
        end_date=end_date,
    )

    return report


def run_multiple_backtests():
    """运行多组参数回测，进行策略优化。"""
    param_grid = [
        # 不同净利润增长阈值
        {"name": "保守型", "net_profit_yoy": "0.15", "roe": "0.06", "top": "15"},
        {"name": "平衡型", "net_profit_yoy": "0.20", "roe": "0.08", "top": "20"},
        {"name": "进取型", "net_profit_yoy": "0.30", "roe": "0.10", "top": "10"},
    ]

    results = []
    for params in param_grid:
        strategy_yaml = f"""
name: ProfitGrowth_{params['name']}
description: 利润增长策略 - {params['name']}

universe:
  type: csi300
  rebalance_freq: 20D

factors:
  profit_growth_score: "net_profit_yoy * 0.3 + roe * 0.25 + revenue_yoy * 0.15"

signals:
  - type: filter
    condition: "net_profit_yoy > {params['net_profit_yoy']}"
  - type: filter
    condition: "roe > {params['roe']}"
  - type: filter
    condition: "revenue_yoy > 0"
  - type: rank
    by: profit_growth_score
    ascending: false
    top: {params['top']}

weights:
  method: equal

risk_control:
  max_position_per_stock: 0.1
  stop_loss: 0.15
  max_drawdown_limit: 0.25

trading_cost:
  commission_rate: 0.0003
  stamp_duty: 0.0005
  slippage_bps: 2.0
"""
        logger.info(f"\n{'='*40}")
        logger.info(f"回测参数: {params['name']}")
        logger.info(f"{'='*40}")

        report = run_backtest(strategy_yaml)
        report["params"] = params
        results.append(report)

        print_report(report)

    # 输出优化结果对比
    print("\n" + "=" * 70)
    print("  参数优化结果对比")
    print("=" * 70)
    print(f"{'策略':<10} {'年化收益':>10} {'最大回撤':>10} {'夏普比率':>10}")
    print("-" * 70)

    for result in results:
        params = result.get("params", {})
        name = params.get("name", "Unknown")
        annual_return = result.get("annual_return", 0) * 100
        max_drawdown = result.get("max_drawdown", 0) * 100
        sharpe = result.get("sharpe_ratio", 0)

        print(f"{name:<10} {annual_return:>9.2f}% {max_drawdown:>9.2f}% {sharpe:>10.2f}")

    print("=" * 70 + "\n")

    return results


def main():
    """主函数。"""
    logger.info("利润增长策略回测")
    logger.info("=" * 50)

    # 运行基础回测
    logger.info("运行基础回测...")
    report = run_backtest(PROFIT_GROWTH_STRATEGY)
    print_report(report)

    # 运行参数优化
    logger.info("\n运行参数优化...")
    results = run_multiple_backtests()

    # 输出 JSON 结果（用于后续分析）
    output_file = project_root / "backtest_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    logger.info(f"结果已保存到: {output_file}")


if __name__ == "__main__":
    main()
