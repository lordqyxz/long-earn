#!/usr/bin/env python3
"""并行网格扫描「质量动量 + 池相对强度门控哑铃」策略参数。

动机：absolute 哑铃 W120_512890 训练集 +30.08% 但 OOS fold 0（2025Q1）
-30.66% 被稳定性门拒绝。归因：指数横盘期（指数在 120 日均线上方，
regime=牛）质量动量池风格性崩盘——指数绝对 MA 门防市场级崩盘，防不了
风格崩盘。本脚本扫描 relataive/combined 门控（池动量 vs 指数动量）。

设计：
- 股票腿固定为质量动量最优参数（W20 动量 + ROE>12% + 净利同比>20% +
  Top5 + 20日调仓 + 15% 止损）
- 门控维度：mode（relative/combined）× rel_window（10/20/60）×
  rel_margin（0/5%/10%），对照 = 上轮冠军 absolute W120
- 防守腿固定红利低波 ETF 512890（上轮排序第一），benchmark 沪深300

评估窗口与铁律：近6月 + 训练集全程（不触测试/验证集）。

用法:
    uv run python scripts/grid_search_barbell_rel.py
    uv run python scripts/grid_search_barbell_rel.py --max-workers 8
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

from long_earn.backtest.engine.parallel import ParallelRunner  # noqa: E402
from long_earn.config import AppConfig  # noqa: E402
from long_earn.context_init import initialize_context  # noqa: E402
from long_earn.core.storage import get_data_dir  # noqa: E402

# 数据分割铁律：只在训练集内评估
RECENT_START = "2024-07-01"
RECENT_END = "2024-12-31"
TRAIN_START = "2022-01-01"
TRAIN_END = "2024-12-31"

BENCHMARK = "000300.SH"
DEFENSIVE = "512890.SH"

MODES = ["relative", "combined"]
REL_WINDOWS = [10, 20, 60]
REL_MARGINS = [0.0, 0.05, 0.10]

STOCK_LEG_YAML = """\
name: {name}
description: {desc}
universe:
  type: main_board+gem
  rebalance_freq: 20D
operator_factors:
  - op: returns
    alias: mom
    params: {{ field: close, period: 20 }}
signals:
  - type: operator
    op: filter_threshold
    params: {{ field: roe, op: ">", value: 0.12 }}
  - type: operator
    op: filter_threshold
    params: {{ field: net_profit_yoy, op: ">", value: 0.2 }}
  - type: operator
    op: rank_top
    params: {{ field: mom, ascending: false, top: 5 }}
weights:
  method: equal
risk_control:
  max_position_per_stock: 0.25
  stop_loss: 0.15
  max_drawdown_limit: 0.3
trading_cost:
  commission_rate: 0.0003
  stamp_duty: 0.0005
  slippage_bps: 2.0
regime:
  benchmark: "{benchmark}"
  window: 120
  mode: {mode}
  rel_window: {rel_window}
  rel_margin: {rel_margin}
  defensive_assets: ["{defensive}"]
"""


def build_yamls() -> list[tuple[str, str]]:
    """构造全部策略 YAML：absolute 冠军对照 + 18 组相对强度组合。"""
    combos: list[tuple[str, str]] = []

    def render(name: str, desc: str, mode: str, rw: int, rm: float) -> str:
        return STOCK_LEG_YAML.format(
            name=name,
            desc=desc,
            benchmark=BENCHMARK,
            mode=mode,
            rel_window=rw,
            rel_margin=rm,
            defensive=DEFENSIVE,
        )

    combos.append(
        (
            "对照:abs_W120",
            render(
                "QM_ABS_W120",
                "对照：absolute 120日均线门控（上轮冠军）",
                "absolute",
                20,
                0.0,
            ),
        )
    )
    for mode in MODES:
        for rw in REL_WINDOWS:
            for rm in REL_MARGINS:
                tag = f"{mode[:3]}_rw{rw}_m{int(rm * 100)}"
                combos.append(
                    (
                        tag,
                        render(
                            f"QM_{tag}",
                            f"质量动量+{mode}门控 rw={rw} margin={rm}",
                            mode,
                            rw,
                            rm,
                        ),
                    )
                )
    return combos


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

    provider = ctx.data_provider
    symbols = provider.get_symbols("main_board+gem", RECENT_END)
    print(f"main_board+gem 股票池: {len(symbols)} 只")
    print(f"并发 worker 数: {max_workers}")
    print(f"近6月窗口: {RECENT_START} ~ {RECENT_END}")
    print(f"训练集对照: {TRAIN_START} ~ {TRAIN_END}")
    print(f"benchmark: {BENCHMARK} / 防守腿: {DEFENSIVE}")
    print("=" * 118)

    combos = build_yamls()
    yamls = [y for _, y in combos]
    print(f"策略组合: {len(combos)} 个（1 absolute 对照 + {len(combos) - 1} 相对强度）")
    print("=" * 118)

    runner = ParallelRunner(
        max_workers=max_workers,
        data_provider=ctx.data_provider,
    )

    print(f"\n[阶段 1] 近6月回测（{len(yamls)} 组合并发）...")
    t0 = time.time()
    recent_outcomes = runner.run_candidates(
        strategy_yamls=yamls,
        start_date=RECENT_START,
        end_date=RECENT_END,
        symbols=symbols,
        benchmark_symbol=BENCHMARK,
    )
    print(f"阶段 1 完成: {time.time() - t0:.1f}s")

    print(f"\n[阶段 2] 完整训练集回测（{TRAIN_START} ~ {TRAIN_END}）...")
    t0 = time.time()
    runner_full = ParallelRunner(
        max_workers=min(max_workers, 8),
        data_provider=ctx.data_provider,
    )
    train_outcomes = runner_full.run_candidates(
        strategy_yamls=yamls,
        start_date=TRAIN_START,
        end_date=TRAIN_END,
        symbols=symbols,
        benchmark_symbol=BENCHMARK,
    )
    print(f"阶段 2 完成: {time.time() - t0:.1f}s")

    print()
    print("=" * 118)
    print("相对强度门控对照汇总 — 近6月 vs 训练集全程")
    print("=" * 118)
    print(f"{'组合':<24} {'近6月收益':>10} {'训练收益':>10} {'训练夏普':>9} "
          f"{'训练回撤':>10} {'可信':>5}")
    print("-" * 118)

    best: tuple[str, object, object] | None = None
    best_score = -999.0
    for (desc, _), r, t in zip(combos, recent_outcomes, train_outcomes, strict=True):
        r_ret = r.total_return if r.success else None
        t_ret = t.total_return if t.success else None
        t_sharpe = t.sharpe_ratio if t.success else None
        t_dd = t.max_drawdown if t.success else None
        t_unreliable = t.metrics_unreliable if t.success else True

        r_str = fmt_pct(r_ret) if r_ret is not None else "ERR"
        t_str = fmt_pct(t_ret) if t_ret is not None else "ERR"
        s_str = f"{t_sharpe:.3f}" if t_sharpe is not None else "ERR"
        d_str = fmt_pct(t_dd) if t_dd is not None else "ERR"
        flag = "否" if t_unreliable else "是"
        print(f"{desc:<24} {r_str:>10} {t_str:>10} {s_str:>9} {d_str:>10} {flag:>5}")

        if not t_unreliable and t_ret is not None and t_ret > best_score:
            best_score = t_ret
            best = (desc, r, t)

    print()
    print("=" * 118)
    print("最终结论")
    print("=" * 118)
    best_path = get_data_dir() / "best_barbell_rel_strategy.yaml"
    if best is not None:
        desc, r, t = best
        print(f"训练集全程最优: {desc}")
        print(f"   训练集: 收益 {fmt_pct(t.total_return)}, "
              f"夏普 {t.sharpe_ratio:.3f}, 回撤 {fmt_pct(t.max_drawdown)}")
        print(f"   近6月:  收益 {fmt_pct(r.total_return)}, "
              f"夏普 {r.sharpe_ratio:.3f}, 回撤 {fmt_pct(r.max_drawdown)}")
        yaml_str = dict(combos)[desc]
        best_path.write_text(yaml_str, encoding="utf-8")
        print(f"\n最优策略已保存: {best_path}")
        print("\n最优策略 YAML:")
        print(yaml_str)
    else:
        print("无可信结果（全部失败或指标不可信），未保存策略")


if __name__ == "__main__":
    main()
