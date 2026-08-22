#!/usr/bin/env python3
"""并行网格扫描纯动量策略参数（质量动量策略的基线对照）。

利用 ParallelRunner（ProcessPoolExecutor + mmap IPC 文件共享数据底座）：
- 主进程预取数据一次（PG 缓存优先）
- worker 进程并发回测，禁用 xtquant（纯缓存，不触发外部下载）
- 18 组合并发执行，总耗时 ≈ 单次回测时间

策略走算子目录路径（ADR-009：旧式 factors/filter/rank DSL 已退役）。
评估窗口遵守数据分割铁律：参数选择只在训练集内。

用法:
    uv run python scripts/grid_search_momentum.py
    uv run python scripts/grid_search_momentum.py --max-workers 8
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

# Windows 中文乱码修复：spawn worker 子进程不继承主进程编码，脚本入口先切 UTF-8
from long_earn.core.stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

# 静默噪声日志（xtquant/portfolio WARNING 会淹没进度行 + 拖慢 I/O）
from loguru import logger as _loguru_logger  # noqa: E402

_loguru_logger.remove()
_loguru_logger.add(sys.stderr, level="ERROR")

from long_earn.backtest.engine.parallel import (  # noqa: E402
    GridResult,
    ParallelRunner,
)
from long_earn.backtest.engine.param_grid import (  # noqa: E402
    ParamGrid,
    render_template,
)
from long_earn.config import AppConfig  # noqa: E402
from long_earn.context_init import initialize_context  # noqa: E402
from long_earn.core.storage import get_data_dir  # noqa: E402

# 数据分割铁律：参数选择只能在训练集内（2022-01-01 ~ 2024-12-31）。
# 近6月 = 训练集最后 6 个月；训练集对照 = 完整训练集。
# 旧版曾用 2026 年窗口（测试集/验证集区间）做参数排序，违反铁律 #2/#3，已修正。
RECENT_START = "2024-07-01"
RECENT_END = "2024-12-31"
TRAIN_START = "2022-01-01"
TRAIN_END = "2024-12-31"

BENCHMARK = "000300.SH"
TOP_N = 10

# 纯动量模板（算子目录路径）：{{ var }} 标量插值
STRATEGY_TEMPLATE = """\
name: Mom{{ window }}_Top{{ top }}_RB{{ rb }}_S{{ stop_pct }}
description: {{ window }}日动量 top{{ top }} 等权 {{ rb }}日调仓 止损{{ stop_pct }}
universe:
  type: csi300
  rebalance_freq: {{ rb }}D
operator_factors:
  - op: returns
    alias: mom
    params: { field: close, period: {{ window }} }
signals:
  - type: operator
    op: filter_threshold
    params: { field: mom, op: ">", value: 0 }
  - type: operator
    op: rank_top
    params: { field: mom, ascending: false, top: {{ top }} }
weights:
  method: equal
risk_control:
  max_position_per_stock: 0.3
  stop_loss: {{ stop_pct }}
  max_drawdown_limit: 0.3
trading_cost:
  commission_rate: 0.0003
  stamp_duty: 0.0005
  slippage_bps: 2.0
"""


def fmt_pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def main() -> None:
    max_workers = os.cpu_count() or 4
    if "--max-workers" in sys.argv:
        idx = sys.argv.index("--max-workers")
        max_workers = int(sys.argv[idx + 1])

    config = AppConfig.from_env()
    config.backtest_start_date = TRAIN_START
    config.backtest_end_date = TRAIN_END
    ctx = initialize_context(config)
    symbols = ctx.data_provider.get_symbols("csi300", RECENT_END)
    print(f"csi300 股票池: {len(symbols)} 只")
    print(f"并发 worker 数: {max_workers}")
    print(f"近6月窗口: {RECENT_START} ~ {RECENT_END}")
    print(f"训练集对照窗口: {TRAIN_START} ~ {TRAIN_END}")
    print("=" * 100)

    # 参数网格：标量插值（stop_pct 与质量动量网格对齐，保证可比性）
    param_grid = ParamGrid(
        scalars={
            "window": [10, 20, 60],
            "top": [3, 5, 10],
            "rb": [10, 20],
            "stop_pct": [0.15, 0.5],
        }
    )
    total = param_grid.total_combinations
    print(f"网格规模: {total} 组合（并发执行）")
    print("=" * 100)

    runner = ParallelRunner(
        max_workers=max_workers,
        data_provider=ctx.data_provider,
    )

    # 阶段 1：近6月全扫
    print(f"\n[阶段 1] 近6月回测（{total} 组合并发）...")
    t0 = time.time()
    recent_result: GridResult = runner.run_grid(
        strategy_template=STRATEGY_TEMPLATE,
        param_grid=param_grid,
        start_date=RECENT_START,
        end_date=RECENT_END,
        symbols=symbols,
        benchmark_symbol=BENCHMARK,
    )
    print(f"阶段 1 完成: {time.time() - t0:.1f}s, "
          f"{recent_result.success_count}/{total} 成功")

    # 按 total_return 排序
    recent_valid = [
        o for o in recent_result.outcomes
        if o.success and not o.metrics_unreliable
    ]
    recent_valid.sort(key=lambda o: o.total_return, reverse=True)

    print()
    print("=" * 100)
    print("阶段 1 结果 — 按近6个月收益率排序（仅指标可信）")
    print("=" * 100)
    print(f"{'task_id':<8} {'param_desc':<30} {'收益':>10} {'夏普':>8} "
          f"{'回撤':>10} {'calmar':>8} {'交易天数':>8}")
    print("-" * 90)
    for o in recent_valid:
        print(f"{o.task_id:<8} {o.param_desc:<30} "
              f"{fmt_pct(o.total_return):>10} {o.sharpe_ratio:>8.3f} "
              f"{fmt_pct(o.max_drawdown):>10} {o.calmar_ratio:>8.3f} "
              f"{o.trading_days:>8}")
    failed = [o for o in recent_result.outcomes if not o.success]
    if failed:
        print(f"\n失败组合 ({len(failed)}):")
        for o in failed:
            print(f"  {o.task_id}: {o.error[:80]}")

    if not recent_valid:
        print("\n无可信结果，退出。")
        return

    # 阶段 2：Top N 跑完整训练集对照（防过拟合）
    top_n = recent_valid[:TOP_N]
    print()
    print(f"[阶段 2] Top {len(top_n)} 训练集对照 ({TRAIN_START} ~ {TRAIN_END}) ...")
    t0 = time.time()
    # run_grid 的 task_id 即 expand_scalars() 展开序号，据此还原组合参数
    scalar_combos = param_grid.expand_scalars()
    top_yaml_combos: list[tuple[str, dict]] = []
    for o in top_n:
        combo = scalar_combos[int(o.task_id)]
        top_yaml_combos.append((render_template(STRATEGY_TEMPLATE, combo), combo))
    train_outcomes = runner.run_candidates(
        strategy_yamls=[y for y, _ in top_yaml_combos],
        start_date=TRAIN_START,
        end_date=TRAIN_END,
        symbols=symbols,
        benchmark_symbol=BENCHMARK,
    )
    print(f"阶段 2 完成: {time.time() - t0:.1f}s")

    print()
    print(f"{'param_desc':<30} {'近6月收益':>10} {'训练收益':>10} "
          f"{'训练夏普':>10} {'训练回撤':>10} {'过拟合':>8}")
    print("-" * 90)

    best_overall: tuple | None = None
    best_overall_ret = -999.0
    for o, h, (_y, combo) in zip(top_n, train_outcomes, top_yaml_combos,
                                 strict=True):
        t_ret = h.total_return if h.success else None
        t_sharpe = h.sharpe_ratio if h.success else None
        t_dd = h.max_drawdown if h.success else None
        t_unreliable = h.metrics_unreliable if h.success else True

        t_ret_str = fmt_pct(t_ret) if t_ret is not None else "ERR"
        t_sharpe_str = f"{t_sharpe:.3f}" if t_sharpe is not None else "ERR"
        t_dd_str = fmt_pct(t_dd) if t_dd is not None else "ERR"
        # 过拟合判定：近6月高收益但训练集亏损
        overfit = "是" if (
            o.total_return > 0.10 and t_ret is not None and t_ret < 0
        ) else "否"

        print(f"{o.param_desc:<30} {fmt_pct(o.total_return):>10} "
              f"{t_ret_str:>10} {t_sharpe_str:>10} {t_dd_str:>10} {overfit:>8}")

        # 选最佳：优先近6月高收益 + 训练集不亏损（稳健）
        robust = not t_unreliable and t_ret is not None and t_ret >= 0
        if robust and o.total_return > best_overall_ret:
            best_overall_ret = o.total_return
            best_overall = (o, h, combo)

    print()
    print("=" * 100)
    print("最终结论")
    print("=" * 100)

    best_path = get_data_dir() / "best_momentum_strategy.yaml"
    if best_overall is not None:
        r, t, combo = best_overall
        print(f"最佳稳健组合: {r.param_desc}")
        print(f"   近6个月: 收益 {fmt_pct(r.total_return)}, "
              f"夏普 {r.sharpe_ratio:.3f}, 回撤 {fmt_pct(r.max_drawdown)}")
        print(f"   训练集:  收益 {fmt_pct(t.total_return)}, "
              f"夏普 {t.sharpe_ratio:.3f}, 回撤 {fmt_pct(t.max_drawdown)}")
        print("   该组合训练集不亏损，近6月收益最高，过拟合风险较低")
        best_yaml = render_template(STRATEGY_TEMPLATE, combo)
        best_path.write_text(best_yaml, encoding="utf-8")
        print(f"\n最佳策略已保存: {best_path}")
        print("\n最佳策略 YAML:")
        print(best_yaml)
    else:
        r = recent_valid[0]
        print("所有 Top 组合在训练集均亏损，无法选出稳健组合")
        print(f"  近6月收益最高: {r.param_desc} = {fmt_pct(r.total_return)}")
        print("  但训练集亏损，存在过拟合/动量均值回归风险，不建议直接采用")
        combo = scalar_combos[int(r.task_id)]
        best_yaml = render_template(STRATEGY_TEMPLATE, combo)
        best_path.write_text(best_yaml, encoding="utf-8")
        print(f"\n近6月最佳策略已保存（附风险提示）: {best_path}")


if __name__ == "__main__":
    main()
