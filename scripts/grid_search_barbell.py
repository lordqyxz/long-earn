#!/usr/bin/env python3
"""并行网格扫描「质量动量 + 牛熊门控哑铃」策略参数。

动机：质量动量策略训练集全程 -27.86%（2022-2023 熊市满仓挨打），动量崩溃
文献（Daniel & Moskowitz 2016）的标准解法是市场状态门控——牛市满仓股票，
熊市切换防守资产。Faber (2007) 的均线择时模型是最经典实现。

设计：
- 股票腿固定为网格搜索最优质量动量参数（W20 动量 + ROE>12% + 净利同比>20%
  + Top5 + 20日调仓 + 15% 止损，近6月 +20.31%）
- 门控维度扫描：均线窗口（120/200/250）× 防守腿（低波红利ETF / 十年国债ETF /
  空仓持币），外加纯股票对照组
- benchmark = 沪深300（指数行情已入 PG 缓存）

评估窗口与铁律：近6月（训练集末 6 月）+ 训练集全程对照，不触测试/验证集。

用法:
    uv run python scripts/grid_search_barbell.py
    uv run python scripts/grid_search_barbell.py --max-workers 8
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

# 防守腿候选：低波红利ETF / 十年国债ETF / 空仓持币
DEFENSIVES: list[tuple[str, str]] = [
    ("512890.SH", "红利低波ETF"),
    ("511260.SH", "十年国债ETF"),
    ("", "空仓持币"),
]

# 均线窗口：120（半年）/ 200（经典年线）/ 250（Faber 10月线）
WINDOWS = [120, 200, 250]

# 股票腿：质量动量网格搜索最优参数（近6月 +20.31% 那组）
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
{regime_block}"""


def _regime_block(window: int, defensive: str) -> str:
    """渲染 regime 配置块（防守腿为空 = 熊市空仓）。"""
    assets = f'["{defensive}"]' if defensive else "[]"
    return (
        "regime:\n"
        f'  benchmark: "{BENCHMARK}"\n'
        f"  window: {window}\n"
        f"  defensive_assets: {assets}\n"
    )


def build_yamls() -> list[tuple[str, str]]:
    """构造全部策略 YAML：纯股票对照 + 9 组哑铃组合。

    Returns:
        [(param_desc, yaml_str), ...]
    """
    combos: list[tuple[str, str]] = [
        (
            "纯股票(无门控)",
            STOCK_LEG_YAML.format(
                name="QM_Baseline", desc="质量动量对照（无牛熊门控）",
                regime_block="",
            ),
        )
    ]
    for window in WINDOWS:
        for defensive, dname in DEFENSIVES:
            tag = defensive if defensive else "CASH"
            desc = f"质量动量+{window}日均线门控 熊市→{dname}"
            combos.append(
                (
                    f"W{window}_{tag}({dname})",
                    STOCK_LEG_YAML.format(
                        name=f"BB_W{window}_{tag}",
                        desc=desc,
                        regime_block=_regime_block(window, defensive),
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
    print(f"benchmark: {BENCHMARK}（门控均线 + 指标计算）")
    print("=" * 118)

    combos = build_yamls()
    yamls = [y for _, y in combos]
    print(f"策略组合: {len(combos)} 个（1 对照 + 9 哑铃）")
    for desc, _ in combos:
        print(f"  - {desc}")
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

    # 训练集全程面板约为近6月的 6 倍，限制 worker 数防内存峰值超限
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
    print("哑铃对照汇总 — 近6月 vs 训练集全程（2022-2023 熊市保护效果）")
    print("=" * 118)
    print(f"{'组合':<26} {'近6月收益':>10} {'训练收益':>10} {'训练夏普':>9} "
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
        print(f"{desc:<26} {r_str:>10} {t_str:>10} {s_str:>9} {d_str:>10} {flag:>5}")

        if not t_unreliable and t_ret is not None:
            score = t_ret
            if score > best_score:
                best_score = score
                best = (desc, r, t)

    print()
    print("=" * 118)
    print("最终结论")
    print("=" * 118)
    best_path = get_data_dir() / "best_barbell_strategy.yaml"
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
