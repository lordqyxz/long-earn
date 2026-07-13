#!/usr/bin/env python3
"""并行网格扫描动量策略参数，寻找最近6个月收益率最佳组合。

利用 ParallelRunner（ProcessPoolExecutor + SharedMemory 共享数据底座）：
- 主进程预取数据一次（DuckDB 缓存优先）
- worker 进程并发回测，禁用 xtquant（纯缓存，不触发财务下载）
- 18 组合并发执行，总耗时 ≈ 单次回测时间

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

# 静默噪声日志（xtquant/portfolio WARNING 会淹没进度行 + 拖慢 I/O）
from loguru import logger as _loguru_logger  # noqa: E402

_loguru_logger.remove()
_loguru_logger.add(sys.stderr, level="ERROR")

from long_earn.config import AppConfig  # noqa: E402
from long_earn.context_init import initialize_context  # noqa: E402
from long_earn.backtest.engine.parallel import ParallelRunner, GridResult  # noqa: E402
from long_earn.backtest.engine.param_grid import ParamGrid  # noqa: E402

RECENT_START = "2026-01-06"
RECENT_END = "2026-07-10"
TRAIN_START = "2023-01-07"
TRAIN_END = "2026-01-05"

# 动量策略模板：{{ var }} 标量插值
STRATEGY_TEMPLATE = """\
name: Mom{{ window }}_Top{{ top }}_RB{{ rb }}
description: {{ window }}日动量 top{{ top }} 等权 {{ rb }}日调仓
universe:
  type: csi300
  rebalance_freq: {{ rb }}D
factors:
  momentum_{{ window }}: close / shift(close, {{ window }}) - 1
  momentum_5: close / shift(close, 5) - 1
signals:
  - type: filter
    condition: momentum_{{ window }} > 0
  - type: rank
    by: momentum_{{ window }}
    ascending: false
    top: {{ top }}
weights:
  method: equal
risk_control:
  max_position_per_stock: 0.3
  stop_loss: 0.15
  max_drawdown_limit: 0.3
trading_cost:
  commission_rate: 0.0003
  stamp_duty: 0.0005
  slippage_bps: 2.0
"""


def fmt_pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def get_csi300_symbols(ctx) -> list[str]:
    """获取 csi300 股票池符号列表。"""
    provider = ctx.data_provider
    if provider is not None:
        return provider.get_symbols("csi300", RECENT_END)
    from long_earn.backtest.data.universe import get_universe_provider  # noqa: PLC0415

    return get_universe_provider().get_symbols("csi300", RECENT_END)


def main() -> None:
    max_workers = os.cpu_count() or 4
    if "--max-workers" in sys.argv:
        idx = sys.argv.index("--max-workers")
        max_workers = int(sys.argv[idx + 1])

    config = AppConfig.from_env()
    config.backtest_start_date = TRAIN_START
    config.backtest_end_date = TRAIN_END
    ctx = initialize_context(config)
    symbols = get_csi300_symbols(ctx)
    print(f"csi300 股票池: {len(symbols)} 只")
    print(f"并发 worker 数: {max_workers}")
    print(f"最近6个月窗口: {RECENT_START} ~ {RECENT_END}")
    print(f"训练集对照窗口: {TRAIN_START} ~ {TRAIN_END}")
    print("=" * 80)

    # 参数网格：标量插值
    param_grid = ParamGrid(
        scalars={
            "window": [10, 20, 60],
            "top": [3, 5, 10],
            "rb": [10, 20],
        }
    )
    total = len(param_grid.expand_scalars())
    print(f"网格规模: {total} 组合（并发执行）")
    print("=" * 80)

    runner = ParallelRunner(
        max_workers=max_workers,
        data_provider=ctx.data_provider,
    )

    # 阶段 1：近6月全扫
    print(f"\n[阶段 1] 最近6个月回测（{total} 组合并发）...")
    t0 = time.time()
    recent_result: GridResult = runner.run_grid(
        strategy_template=STRATEGY_TEMPLATE,
        param_grid=param_grid,
        start_date=RECENT_START,
        end_date=RECENT_END,
        symbols=symbols,
        benchmark_symbol="000300.SH",
        allow_large_grid=True,
    )
    t1 = time.time()
    print(f"阶段 1 完成: {t1 - t0:.1f}s, "
          f"{recent_result.success_count}/{total} 成功")

    # 按 total_return 排序
    recent_valid = [
        o for o in recent_result.outcomes
        if o.success and not o.metrics_unreliable
    ]
    recent_valid.sort(key=lambda o: o.total_return, reverse=True)

    print()
    print("=" * 80)
    print("阶段 1 结果 — 按最近6个月收益率排序（仅指标可信）")
    print("=" * 80)
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

    # 阶段 2：Top 10 跑训练集对照（防过拟合）
    top10 = recent_valid[:10]
    print()
    print("=" * 80)
    print(f"[阶段 2] Top 10 候选训练集对照 ({TRAIN_START} ~ {TRAIN_END})")
    print("=" * 80)

    # 构造 Top 10 的 param_grid（显式组合）
    top_combos: list[dict] = []
    for o in top10:
        # 从 param_desc 解析参数: "window=20, top=5, rb=20"
        parts = dict(
            kv.strip().split("=") for kv in o.param_desc.split(",")
        )
        top_combos.append({k: int(v) for k, v in parts.items()})

    train_grid = ParamGrid(scalars={})  # 空 scalars → [{}]
    # 用显式组合替代笛卡尔积
    train_grid_combos = top_combos

    # 手动构造 Top 10 的模板渲染 + 并发
    from long_earn.backtest.engine.param_grid import render_template  # noqa: PLC0415

    train_yamls: list[tuple[str, str]] = []
    for i, combo in enumerate(train_grid_combos):
        yaml_str = render_template(STRATEGY_TEMPLATE, combo)
        param_desc = ", ".join(f"{k}={v}" for k, v in combo.items())
        train_yamls.append((yaml_str, param_desc))

    # 预取训练集数据 + 并发
    t2 = time.time()
    train_outcomes = _run_yamls_parallel(
        runner, train_yamls, symbols, TRAIN_START, TRAIN_END, "000300.SH"
    )
    t3 = time.time()
    print(f"阶段 2 完成: {t3 - t2:.1f}s")
    print()
    print(f"{'param_desc':<30} {'近6月收益':>10} {'训练收益':>10} "
          f"{'训练夏普':>10} {'训练回撤':>10} {'过拟合':>8}")
    print("-" * 90)

    best_overall = None
    best_overall_ret = -999
    for i, o in enumerate(top10):
        train_o = train_outcomes[i]
        train_ret = train_o.total_return if train_o.success else None
        train_sharpe = train_o.sharpe_ratio if train_o.success else None
        train_dd = train_o.max_drawdown if train_o.success else None
        train_unreliable = (
            train_o.metrics_unreliable if train_o.success else True
        )

        t_ret_str = fmt_pct(train_ret) if train_ret is not None else "ERR"
        t_sharpe_str = f"{train_sharpe:.3f}" if train_sharpe is not None else "ERR"
        t_dd_str = fmt_pct(train_dd) if train_dd is not None else "ERR"

        # 过拟合判定：近6月高收益但训练集亏损
        overfit = "⚠ 是" if (
            o.total_return > 0.10
            and train_ret is not None
            and train_ret < 0
        ) else "否"

        print(f"{o.param_desc:<30} {fmt_pct(o.total_return):>10} "
              f"{t_ret_str:>10} {t_sharpe_str:>10} {t_dd_str:>10} {overfit:>8}")

        # 选最佳：优先近6月高收益 + 训练集不亏损（稳健）
        if not train_unreliable and train_ret is not None:
            if train_ret >= 0 and o.total_return > best_overall_ret:
                best_overall_ret = o.total_return
                best_overall = (o, train_o, top_combos[i])

    # 最终结论
    print()
    print("=" * 80)
    print("最终结论")
    print("=" * 80)

    if best_overall is not None:
        r, t, combo = best_overall
        print(f"✅ 最佳稳健组合: {r.param_desc}")
        print(f"   最近6个月: 收益 {fmt_pct(r.total_return)}, "
              f"夏普 {r.sharpe_ratio:.3f}, 回撤 {fmt_pct(r.max_drawdown)}")
        print(f"   训练集:     收益 {fmt_pct(t.total_return)}, "
              f"夏普 {t.sharpe_ratio:.3f}, 回撤 {fmt_pct(t.max_drawdown)}")
        print(f"   该组合在训练集不亏损，近6月收益最高，过拟合风险较低")

        from long_earn.backtest.engine.param_grid import render_template  # noqa: PLC0415
        from long_earn.core.storage import best_strategy_path  # noqa: PLC0415

        best_yaml = render_template(STRATEGY_TEMPLATE, combo)
        best_strategy_path().write_text(best_yaml, encoding="utf-8")
        print(f"\n   最佳策略已保存: {best_strategy_path()}")
        print("\n   最佳策略 YAML:")
        print(best_yaml)
    else:
        # 退化：所有 Top10 训练集都亏损 → 报告近6月最佳但标注风险
        r = recent_valid[0]
        print(f"⚠ 所有 Top10 组合在训练集均亏损，无法选出稳健组合")
        print(f"  近6月收益最高: {r.param_desc} = {fmt_pct(r.total_return)}")
        print(f"  但训练集亏损，存在过拟合/动量均值回归风险，不建议直接采用")
        print(f"  建议：降低调仓频率或加入反向信号验证")

        from long_earn.backtest.engine.param_grid import render_template  # noqa: PLC0415
        from long_earn.core.storage import best_strategy_path  # noqa: PLC0415

        parts = dict(kv.strip().split("=") for kv in r.param_desc.split(","))
        combo = {k: int(v) for k, v in parts.items()}
        best_yaml = render_template(STRATEGY_TEMPLATE, combo)
        best_strategy_path().write_text(best_yaml, encoding="utf-8")
        print(f"\n  近6月最佳策略已保存（附风险提示）: {best_strategy_path()}")


def _run_yamls_parallel(
    runner: ParallelRunner,
    yamls: list[tuple[str, str]],
    symbols: list[str],
    start_date: str,
    end_date: str,
    benchmark: str,
) -> list:
    """手动并发执行一组已渲染的 YAML（绕过 ParamGrid，用于 Top10 训练集对照）。"""
    from long_earn.backtest.engine.parallel import (  # noqa: PLC0415
        BacktestTask,
        BacktestOutcome,
        _run_one_backtest,
    )
    from long_earn.backtest.engine.dsl import parse_strategy_yaml  # noqa: PLC0415
    from long_earn.backtest.engine.shared_data import SharedDataContext  # noqa: PLC0415

    full_data = runner._prepare_data(symbols, start_date, end_date)
    if full_data.is_empty():
        return [BacktestOutcome(task_id=str(i), success=False, error="数据预取为空")
                for i in range(len(yamls))]

    first_dsl = parse_strategy_yaml(yamls[0][0])
    stop_loss = first_dsl.risk_control.stop_loss
    max_dd_limit = first_dsl.risk_control.max_drawdown_limit
    max_pos_pct = first_dsl.risk_control.max_position_per_stock

    outcomes: list = []
    with SharedDataContext(full_data) as sctx:
        shm_token, shm_size, pickle_data = sctx.get_worker_args()
        tasks = [
            BacktestTask(
                strategy_yaml=yaml_str,
                start_date=start_date,
                end_date=end_date,
                symbols=symbols,
                benchmark_symbol=benchmark,
                shm_token=shm_token,
                shm_size=shm_size,
                pickle_data=pickle_data,
                stop_loss=stop_loss,
                max_drawdown_limit=max_dd_limit,
                max_position_pct=max_pos_pct,
                task_id=str(idx),
                param_desc=desc,
            )
            for idx, (yaml_str, desc) in enumerate(yamls)
        ]
        if runner.max_workers <= 1:
            outcomes = [_run_one_backtest(t) for t in tasks]
        else:
            from concurrent.futures import ProcessPoolExecutor  # noqa: PLC0415

            with ProcessPoolExecutor(max_workers=runner.max_workers) as ex:
                outcomes = list(ex.map(_run_one_backtest, tasks))
    return outcomes


if __name__ == "__main__":
    main()