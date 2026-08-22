#!/usr/bin/env python3
"""并行网格扫描「动量/反转 × 财务质量」复合选股策略参数。

基于文献调研（BigQuant A股动量实证、Piotroski F-Score、质量动量组合）：
- A股短期价格动量 IC 为负（反转效应显著）→ 动量方向做正反两向扫描
- 质量过滤（ROE、净利润同比）与动量低相关 → 组合互补
- 避免接飞刀 → 财务质量门槛缩小股票池后再排序

耦合参数在模板内用 jinja2 表达式派生（dir → ascending/中文名，pct → 阈值），
笛卡尔积只展开语义独立维度：window × dir × roe_pct × npy_pct × top × rb × stop_pct。
止损做两档（0.15 紧止损 vs 0.5 宽止损近似不止损）：检验紧止损在震荡市的
鞭打效应（whipsaw）对因子组合收益的侵蚀。

评估窗口与 strategy_rd 研究循环一致（铁律：只用训练集）：
- recent = 训练集最后 6 个月（主排序指标）
- history = 完整训练集（防过拟合对照）

用法:
    uv run python scripts/grid_search_quality_momentum.py
    uv run python scripts/grid_search_quality_momentum.py --max-workers 16
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from long_earn.core.stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

from loguru import logger as _loguru_logger  # noqa: E402

_loguru_logger.remove()
_loguru_logger.add(sys.stderr, level="ERROR")

from long_earn.backtest.engine.parallel import GridResult, ParallelRunner  # noqa: E402
from long_earn.backtest.engine.param_grid import (  # noqa: E402
    ParamGrid,
    render_template,
)
from long_earn.config import AppConfig  # noqa: E402
from long_earn.context_init import initialize_context  # noqa: E402
from long_earn.core.storage import get_data_dir  # noqa: E402

# 与 strategy_rd.research_service 的评估窗口一致（训练集内，铁律 #1）
RECENT_START = "2024-07-01"
RECENT_END = "2024-12-31"
TRAIN_START = "2022-01-01"
TRAIN_END = "2024-12-31"

BENCHMARK = "000300.SH"
TOP_N = 10

# 动量/反转 × 财务质量复合模板：{{ var }} 标量插值 + jinja2 派生耦合字段
STRATEGY_TEMPLATE = """\
name: QM_W{{ window }}_{{ dir }}_R{{ roe_pct }}_N{{ npy_pct }}_T{{ top }}_RB{{ rb }}_S{{ stop_pct }}
description: {{ window }}日{{ "动量" if dir == "mom" else "反转" }} + ROE>{{ roe_pct }}% + 净利润同比>{{ npy_pct }}% 选{{ top }}只等权 止损{{ stop_pct }}
universe:
  type: main_board+gem
  rebalance_freq: {{ rb }}D
operator_factors:
  - op: returns
    alias: mom
    params: { field: close, period: {{ window }} }
signals:
  - type: operator
    op: filter_threshold
    params: { field: roe, op: ">", value: {{ roe_pct / 100 }} }
  - type: operator
    op: filter_threshold
    params: { field: net_profit_yoy, op: ">", value: {{ npy_pct / 100 }} }
  - type: operator
    op: rank_top
    params: { field: mom, ascending: {{ "false" if dir == "mom" else "true" }}, top: {{ top }} }
weights:
  method: equal
risk_control:
  max_position_per_stock: 0.25
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

    # 股票池从缓存取（并行回测铁律：worker 只读缓存，禁触 miniqmt）
    provider = ctx.data_provider
    symbols = provider.get_symbols("main_board+gem", RECENT_END)
    print(f"main_board+gem 股票池: {len(symbols)} 只")
    print(f"并发 worker 数: {max_workers}")
    print(f"近6月窗口: {RECENT_START} ~ {RECENT_END}")
    print(f"训练集对照: {TRAIN_START} ~ {TRAIN_END}")
    print("=" * 110)

    # 参数网格：窗口 × 方向(动量/反转) × ROE门槛 × 净利润同比门槛 × 持仓数 × 调仓频率 × 止损
    # window=60 已剔除：旧轮全部 32 个 W60 组合被可信度门过滤（极端涨幅标的
    # 涨跌停/停牌导致订单跳过率>50%），且 A股 3 个月动量 IC 弱，文献不支持
    param_grid = ParamGrid(
        scalars={
            "window": [10, 20],
            "dir": ["mom", "rev"],
            "roe_pct": [8, 12],
            "npy_pct": [20, 50],
            "top": [5, 10],
            "rb": [10, 20],
            "stop_pct": [0.15, 0.5],
        }
    )
    total = param_grid.total_combinations
    print(f"网格规模: {total} 组合（并发执行）")
    print("=" * 110)

    runner = ParallelRunner(
        max_workers=max_workers,
        data_provider=ctx.data_provider,
    )

    print(f"\n[阶段 1] 近6月回测（{total} 组合并发）...")
    t0 = time.time()
    recent_result: GridResult = runner.run_grid(
        strategy_template=STRATEGY_TEMPLATE,
        param_grid=param_grid,
        start_date=RECENT_START,
        end_date=RECENT_END,
        symbols=symbols,
        benchmark_symbol=BENCHMARK,
        allow_large_grid=True,
    )
    print(f"阶段 1 完成: {time.time() - t0:.1f}s, "
          f"{recent_result.success_count}/{total} 成功")

    recent_valid = [
        o for o in recent_result.outcomes
        if o.success and not o.metrics_unreliable
    ]
    recent_valid.sort(key=lambda o: o.total_return, reverse=True)

    print()
    print("=" * 110)
    print("阶段 1 结果 — 按近6个月收益率排序（仅指标可信）")
    print("=" * 110)
    print(f"{'task_id':<8} {'param_desc':<56} {'收益':>10} {'夏普':>8} "
          f"{'回撤':>10} {'交易天数':>8}")
    print("-" * 110)
    for o in recent_valid:
        print(f"{o.task_id:<8} {o.param_desc:<56} "
              f"{fmt_pct(o.total_return):>10} {o.sharpe_ratio:>8.3f} "
              f"{fmt_pct(o.max_drawdown):>10} {o.trading_days:>8}")
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
    print(f"[阶段 2] Top {len(top_n)} 跑完整训练集 {TRAIN_START} ~ {TRAIN_END} ...")
    t0 = time.time()
    # run_grid 的 task_id 即 expand_scalars() 展开序号，据此还原组合参数
    scalar_combos = param_grid.expand_scalars()
    top_yaml_combos: list[tuple[str, dict]] = []
    for o in top_n:
        idx = int(o.task_id)
        combo = scalar_combos[idx]
        top_yaml_combos.append((render_template(STRATEGY_TEMPLATE, combo), combo))
    # 训练集全程面板约为近6月的 6 倍，限制 worker 数防内存峰值超限
    # （每 worker attach 后约 2GB：IPC 字节拷贝 + 解析后的 polars DataFrame）
    runner_full = ParallelRunner(
        max_workers=min(max_workers, 8),
        data_provider=ctx.data_provider,
    )
    history_outcomes = runner_full.run_candidates(
        strategy_yamls=[y for y, _ in top_yaml_combos],
        start_date=TRAIN_START,
        end_date=TRAIN_END,
        symbols=symbols,
        benchmark_symbol=BENCHMARK,
    )
    print(f"阶段 2 完成: {time.time() - t0:.1f}s")

    print()
    print("=" * 110)
    print("最终汇总 — 近6月收益 vs 训练集全程收益（过拟合检查）")
    print("=" * 110)
    print(f"{'param_desc':<56} {'近6月':>10} {'训练全程':>10} "
          f"{'训练夏普':>9} {'训练回撤':>10} {'过拟合':>7}")
    print("-" * 110)

    best_overall: tuple | None = None
    best_overall_ret = -999.0
    for o, h, (_y, combo) in zip(top_n, history_outcomes, top_yaml_combos,
                                 strict=True):
        t_ret = h.total_return if h.success else None
        t_sharpe = h.sharpe_ratio if h.success else None
        t_dd = h.max_drawdown if h.success else None
        t_unreliable = h.metrics_unreliable if h.success else True

        t_ret_str = fmt_pct(t_ret) if t_ret is not None else "ERR"
        t_sharpe_str = f"{t_sharpe:.3f}" if t_sharpe is not None else "ERR"
        t_dd_str = fmt_pct(t_dd) if t_dd is not None else "ERR"
        # 过拟合判定：近6月高收益但训练集全程亏损
        overfit = "是" if (
            o.total_return > 0.10 and t_ret is not None and t_ret < 0
        ) else "否"

        print(f"{o.param_desc:<56} {fmt_pct(o.total_return):>10} "
              f"{t_ret_str:>10} {t_sharpe_str:>9} {t_dd_str:>10} {overfit:>7}")

        # 选最佳：优先近6月高收益 + 训练集全程不亏损（稳健）
        robust = not t_unreliable and t_ret is not None and t_ret >= 0
        if robust and o.total_return > best_overall_ret:
            best_overall_ret = o.total_return
            best_overall = (o, h, combo)

    print()
    print("=" * 110)
    print("最终结论")
    print("=" * 110)

    best_path = get_data_dir() / "best_quality_momentum_strategy.yaml"
    if best_overall is not None:
        r, t, combo = best_overall
        print(f"最佳稳健组合: {r.param_desc}")
        print(f"   近6个月:   收益 {fmt_pct(r.total_return)}, "
              f"夏普 {r.sharpe_ratio:.3f}, 回撤 {fmt_pct(r.max_drawdown)}")
        print(f"   训练集:    收益 {fmt_pct(t.total_return)}, "
              f"夏普 {t.sharpe_ratio:.3f}, 回撤 {fmt_pct(t.max_drawdown)}")
        print("   该组合训练集全程不亏损，近6月收益最高，过拟合风险较低")
        best_yaml = render_template(STRATEGY_TEMPLATE, combo)
        best_path.write_text(best_yaml, encoding="utf-8")
        print(f"\n最佳策略已保存: {best_path}")
        print("\n最佳策略 YAML:")
        print(best_yaml)
    else:
        r = recent_valid[0]
        print("所有 Top 候选在训练集均亏损或指标不可信，无法选出稳健组合")
        print(f"  近6月收益最高: {r.param_desc} = {fmt_pct(r.total_return)}")
        print("  但训练集亏损，存在过拟合/均值回归风险，不建议直接采用")
        idx = int(r.task_id)
        combo = scalar_combos[idx]
        best_yaml = render_template(STRATEGY_TEMPLATE, combo)
        best_path.write_text(best_yaml, encoding="utf-8")
        print(f"\n近6月最佳策略已保存（附风险提示）: {best_path}")


if __name__ == "__main__":
    main()
