#!/usr/bin/env python3
"""直接用回测引擎在最近6个月回测策略，给出真实收益率。

用法:
    uv run python scripts/backtest_recent.py
"""

from __future__ import annotations

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

# Windows 中文乱码修复：脚本入口先切 UTF-8（spawn worker 子进程不继承主进程编码）
from long_earn.core.stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

from long_earn.config import AppConfig  # noqa: E402
from long_earn.context_init import initialize_context  # noqa: E402
from long_earn.services.backtest_service import BacktestServiceImpl  # noqa: E402

STRATEGY_YAML = """\
name: Momentum20Strategy
description: 20日动量选股策略 - 选近20日收益率最高的5只股票等权持仓
universe:
  type: csi300
  rebalance_freq: 20D
factors:
  momentum_20: close / shift(close, 20) - 1
  momentum_5: close / shift(close, 5) - 1
signals:
  - type: filter
    condition: momentum_20 > 0
  - type: rank
    by: momentum_20
    ascending: false
    top: 5
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

RECENT_START = "2026-01-06"
RECENT_END = "2026-07-08"
TRAIN_START = "2023-01-05"
TRAIN_END = "2026-01-05"


def main() -> None:
    config = AppConfig.from_env()
    config.backtest_start_date = TRAIN_START
    config.backtest_end_date = TRAIN_END
    ctx = initialize_context(config)

    bs = BacktestServiceImpl(
        config=config, logger=ctx.logger, data_provider=ctx.data_provider
    )

    print("=" * 64)
    print("策略: Momentum20Strategy（20日动量选股，csi300，top5等权）")
    print("=" * 64)

    # 最近6个月回测
    print(f"\n[最近6个月] {RECENT_START} ~ {RECENT_END}")
    recent = bs.run(
        strategy_yaml=STRATEGY_YAML,
        start_date=RECENT_START,
        end_date=RECENT_END,
    )

    if "error" in recent:
        print(f"  回测失败: {recent.get('error')}")
        print(f"  error_category: {recent.get('error_category')}")
        print(f"  error_detail: {recent.get('error_detail', '')[:300]}")
    else:
        print(f"  总收益率:   {recent.get('total_return', 0):.4f} ({recent.get('total_return', 0)*100:.2f}%)")
        print(f"  年化收益率: {recent.get('annual_return', 0):.4f} ({recent.get('annual_return', 0)*100:.2f}%)")
        print(f"  夏普比率:   {recent.get('sharpe_ratio', 0):.4f}")
        print(f"  最大回撤:   {recent.get('max_drawdown', 0):.4f} ({recent.get('max_drawdown', 0)*100:.2f}%)")
        print(f"  胜率:       {recent.get('win_rate', 0):.4f}")
        print(f"  交易天数:   {recent.get('trading_days', 0)}")
        print(f"  波动率:     {recent.get('volatility', 0):.4f}")
        print(f"  calmar:     {recent.get('calmar_ratio', 0):.4f}")
        print(f"  sortino:    {recent.get('sortino_ratio', 0):.4f}")
        diag = recent.get("strategy_diagnostics", {})
        print(f"  trade_count: {diag.get('trade_count', 0)}")
        print(f"  metrics_unreliable: {recent.get('metrics_unreliable', False)}")
        if diag.get("factor_failures"):
            print(f"  factor_failures: {len(diag['factor_failures'])} 次")
        if diag.get("step_failures"):
            print(f"  step_failures: {len(diag['step_failures'])} 次")

    # 训练集回测（对比）
    print(f"\n[训练集对照] {TRAIN_START} ~ {TRAIN_END}")
    train = bs.run(
        strategy_yaml=STRATEGY_YAML,
        start_date=TRAIN_START,
        end_date=TRAIN_END,
    )
    if "error" in train:
        print(f"  回测失败: {train.get('error')}")
    else:
        print(f"  总收益率:   {train.get('total_return', 0):.4f} ({train.get('total_return', 0)*100:.2f}%)")
        print(f"  年化收益率: {train.get('annual_return', 0):.4f} ({train.get('annual_return', 0)*100:.2f}%)")
        print(f"  夏普比率:   {train.get('sharpe_ratio', 0):.4f}")
        print(f"  最大回撤:   {train.get('max_drawdown', 0):.4f} ({train.get('max_drawdown', 0)*100:.2f}%)")
        print(f"  交易天数:   {train.get('trading_days', 0)}")
        print(f"  metrics_unreliable: {train.get('metrics_unreliable', False)}")

    print()
    print("=" * 64)
    print("结论")
    print("=" * 64)
    if "error" not in recent and "error" not in train:
        r_ret = recent.get("total_return", 0)
        t_ret = train.get("total_return", 0)
        print(f"  最近6个月总收益率: {r_ret*100:.2f}%")
        print(f"  训练集总收益率:     {t_ret*100:.2f}%")
        if not recent.get("metrics_unreliable", True):
            print("  指标可信: 是")
        else:
            print("  指标可信: 否（策略可能退化，需检查因子/信号失败）")

    # 保存策略到数据目录（统一存储位置）
    from long_earn.core.storage import best_strategy_path

    out = best_strategy_path()
    out.write_text(STRATEGY_YAML, encoding="utf-8")
    print(f"\n策略已保存到: {out.resolve()}")


if __name__ == "__main__":
    main()
