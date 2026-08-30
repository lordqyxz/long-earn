#!/usr/bin/env python3
"""用回测引擎回测数据目录最佳策略：训练集尾部窗口 + 训练集全程对照。

窗口全部从 AppConfig 三段式分割派生（遵守量化数据分割铁律，不触碰测试/验证集）：
- "recent" 窗口 = 训练集尾部（train_end - 183 天 ~ train_end）
- 对照窗口     = 训练集全程（train_start_date ~ train_end_date）

策略源：core.storage.best_strategy_path()（数据目录权威副本）。
文件不存在时报错退出；本脚本不再内嵌策略、也不再覆写基准文件。

用法:
    uv run python scripts/backtest_recent.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

# Windows 中文乱码修复：脚本入口先切 UTF-8（spawn worker 子进程不继承主进程编码）
from long_earn.core.stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

from long_earn.config import AppConfig  # noqa: E402
from long_earn.context_init import initialize_context  # noqa: E402
from long_earn.core.storage import best_strategy_path  # noqa: E402
from long_earn.services.backtest_service import BacktestServiceImpl  # noqa: E402

# 训练集尾部 "recent" 窗口长度（自然日）
RECENT_WINDOW_DAYS = 183


def main() -> None:
    config = AppConfig.from_env()
    train_start = config.train_start_date
    train_end = config.train_end_date
    recent_start = (
        date.fromisoformat(train_end) - timedelta(days=RECENT_WINDOW_DAYS)
    ).isoformat()
    config.backtest_start_date = train_start
    config.backtest_end_date = train_end

    # 策略源：数据目录权威副本（core.storage 裁决），缺失即报错退出
    yaml_path = best_strategy_path()
    if not yaml_path.exists():
        print(f"[错误] 最佳策略文件不存在: {yaml_path}")
        print("请先运行 scripts/find_best_strategy.py 产出最佳策略。")
        sys.exit(1)
    strategy_yaml = yaml_path.read_text(encoding="utf-8")

    ctx = initialize_context(config)

    bs = BacktestServiceImpl(
        config=config, logger=ctx.logger, data_provider=ctx.data_provider
    )

    print("=" * 64)
    print(f"策略源: {yaml_path}")
    print("=" * 64)

    # 训练集尾部窗口回测
    print(f"\n[训练集尾部 recent] {recent_start} ~ {train_end}")
    recent = bs.run(
        strategy_yaml=strategy_yaml,
        start_date=recent_start,
        end_date=train_end,
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

    # 训练集全程回测（对照）
    print(f"\n[训练集全程对照] {train_start} ~ {train_end}")
    train = bs.run(
        strategy_yaml=strategy_yaml,
        start_date=train_start,
        end_date=train_end,
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
        print(f"  训练集尾部窗口总收益率: {r_ret*100:.2f}%")
        print(f"  训练集全程总收益率:     {t_ret*100:.2f}%")
        if not recent.get("metrics_unreliable", True):
            print("  指标可信: 是")
        else:
            print("  指标可信: 否（策略可能退化，需检查因子/信号失败）")


if __name__ == "__main__":
    main()
