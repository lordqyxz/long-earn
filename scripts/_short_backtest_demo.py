"""临时脚本：简短回测（约 2 个月），验证并预览交易明细的买入卖出原因。

跑完打印 run_id 与 trade journal（含 reason），方便在 Web 页面对照查看。
"""

from __future__ import annotations

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

import yaml  # noqa: E402
from long_earn.app.analyzer import BacktestAnalyzer  # noqa: E402
from long_earn.config import AppConfig  # noqa: E402
from long_earn.context_init import create_runtime_context  # noqa: E402
from long_earn.services.backtest_service import BacktestServiceImpl  # noqa: E402

STRATEGY_YAML = """\
name: Momentum20Short
description: 20日动量选股策略（简短演示）- 选近20日收益率最高的5只股票等权持仓
universe:
  type: csi300
  rebalance_freq: 20D
operator_factors:
  - op: returns
    alias: mom
    params: { field: close, period: 20 }
signals:
  - type: operator
    op: filter_threshold
    params: { field: mom, op: ">", value: 0.0 }
  - type: operator
    op: rank_top
    params: { field: mom, top: 5, ascending: false }
weights:
  method: equal
risk_control:
  max_position_per_stock: 0.25
  stop_loss: 0.1
  max_drawdown_limit: 0.2
trading_cost:
  commission_rate: 0.0003
  stamp_duty: 0.0005
  slippage_bps: 2.0
"""

# 简短窗口：约 2 个月（覆盖约 2 次 20D 再平衡）
START = "2026-06-01"
END = "2026-07-31"


def main() -> None:
    config = AppConfig.from_env()
    config.backtest_start_date = START
    config.backtest_end_date = END
    ctx = create_runtime_context(config)

    bs = BacktestServiceImpl(
        config=config, logger=ctx.logger, data_provider=ctx.data_provider
    )

    print("=" * 64)
    print(f"简短回测: Momentum20Short  {START} ~ {END}")
    print("=" * 64)
    result = bs.run(strategy_yaml=STRATEGY_YAML, start_date=START, end_date=END)

    if "error" in result:
        print(f"回测失败: {result.get('error')}")
        print(f"  error_category: {result.get('error_category')}")
        print(f"  error_detail: {result.get('error_detail', '')[:400]}")
        raise SystemExit(1)

    print(f"总收益率:   {result.get('total_return', 0) * 100:.2f}%")
    print(f"年化收益率: {result.get('annual_return', 0) * 100:.2f}%")
    print(f"最大回撤:   {result.get('max_drawdown', 0) * 100:.2f}%")
    print(f"夏普比率:   {result.get('sharpe_ratio', 0):.4f}")
    print(f"胜率:       {result.get('win_rate', 0):.4f}")
    print(f"交易天数:   {result.get('trading_days', 0)}")
    diag = result.get("strategy_diagnostics", {})
    print(f"trade_count: {diag.get('trade_count', 0)}")
    print()

    # 找最新 run_id 并打印交易明细（含 reason）
    analyzer = BacktestAnalyzer()
    runs = analyzer.get_runs_summary()
    if not runs:
        print("未在审计库找到 run，无法预览交易明细。")
        raise SystemExit(1)
    latest = runs[0] if isinstance(runs[0], dict) else runs[0].to_dicts()[0]
    run_id = latest.get("run_id") or latest.get("runId") or latest.get("id")
    print(f"最新 run_id: {run_id}")
    print(f"run 时间:    {latest.get('timestamp') or latest.get('time') or latest.get('created_at')}")

    journal = analyzer.export_trade_journal(run_id)
    print(f"\n交易明细（{len(journal)} 笔，含原因）:")
    print("-" * 80)
    for t in journal:
        print(
            f"  {t.get('time')}  {t.get('symbol'):<12} "
            f"{'买入' if t.get('type') == 'BUY' else '卖出':<4} "
            f"x{t.get('quantity'):>8.0f}  @{t.get('price'):>8.2f}  "
            f"| 原因: {t.get('reason') or '—'}"
        )

    print()
    print(f"请到 Web 页面的回测结果中查看 run_id={run_id} 的交易明细与个股图表。")


if __name__ == "__main__":
    main()
