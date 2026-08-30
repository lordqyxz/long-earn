#!/usr/bin/env python3
"""对比当前最佳策略与候选新策略的回测指标（多核并行加速版）。

严格遵守量化数据分割规范（三段窗口见 AppConfig 默认值/环境变量）：
- 训练集（config.train_*）：策略研发、参数寻优，可自由使用（本脚本仅用训练集）
- 测试集（config.test_*）：仅 HTR 合并门，本脚本不触碰
- 验证集（config.validation_*）：开发阶段禁止使用

加速方案：
1. ParallelRunner.run_candidates 预取数据一次，mmap IPC 文件共享给全部策略
2. 每个策略 YAML 独立解析风控参数与 warmup（候选自带 risk_control 生效，
   基准与候选互不污染，A/B 对照语义真实成立）
3. ProcessPoolExecutor 并行回测，worker 进程日志降噪（ERROR 级别）

用法:
    uv run python scripts/compare_strategies.py
    uv run python scripts/compare_strategies.py --max-workers 4
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

# Windows 中文乱码修复：脚本入口先切 UTF-8（spawn worker 子进程不继承主进程编码）
from long_earn.core.stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

# 主进程日志降噪（减少 I/O 开销）
from loguru import logger as _loguru_logger  # noqa: E402

_loguru_logger.remove()
_loguru_logger.add(sys.stderr, level="ERROR")

from long_earn.backtest.data.cache import DataCache  # noqa: E402
from long_earn.backtest.data.polars_adapter import (  # noqa: E402
    PandasToPolarsProvider,
)
from long_earn.backtest.engine.parallel import ParallelRunner  # noqa: E402
from long_earn.config import AppConfig  # noqa: E402
from long_earn.context_init import initialize_context  # noqa: E402
from long_earn.core.storage import best_strategy_path  # noqa: E402

# ── 候选策略 A：短期反转 + 中期动量 + 低波 + EP + ROE + 净利润同比 ─────
# 核心假设：A 股短期反转效应稳健，用 5 日反转替换 120 日动量（避免与 60d 重复），
# 移除 BP（与 EP 高度相关），保持 6 因子等权，但因子构成更均衡（反转+动量+波动+估值+盈利+成长）。
CANDIDATE_A = """\
strategy:
  name: ReversalMomentumHybrid
  description: 短期反转+中期动量+低波+估值+盈利+成长复合因子策略，利用A股短期反转效应与中期动量互补，6因子等权合成
  universe:
    type: csi500
    rebalance_freq: 20D
  start_date: 2022-01-01
  end_date: 2024-12-31
  operator_factors:
    # 1. 短期反转：5日收益率取负（过度反应后回归）
    - op: returns
      alias: rev_5
      params: { field: close, period: 5 }
    - op: arithmetic
      alias: reversal
      params: { lhs: rev_5, rhs: -1, op: '*' }
    # 2. 中期动量：60日收益率（趋势延续）
    - op: returns
      alias: mom_60
      params: { field: close, period: 60 }
    # 3. 波动率：20日收益率标准差取负值
    - op: returns
      alias: ret_20
      params: { field: close, period: 20 }
    - op: windowed
      alias: vol_20
      params: { field: ret_20, window: 20, agg: std }
    - op: arithmetic
      alias: neg_vol
      params: { lhs: vol_20, rhs: -1, op: '*' }
    # 4. 估值因子：PE倒数
    - op: arithmetic
      alias: ep
      params: { lhs: eps, rhs: close, op: '/' }
    # 5. 盈利因子：ROE
    # 6. 成长因子：净利润同比
    # —— 时间序列标准化（过去60日滚动z-score）——
    # 对 reversal
    - op: windowed
      alias: reversal_mean
      params: { field: reversal, window: 60, agg: mean }
    - op: windowed
      alias: reversal_std
      params: { field: reversal, window: 60, agg: std }
    - op: arithmetic
      alias: reversal_z
      params: { lhs: reversal, rhs: reversal_mean, op: '-' }
    - op: arithmetic
      alias: reversal_z_scaled
      params: { lhs: reversal_z, rhs: reversal_std, op: '/' }
    # 对 mom_60
    - op: windowed
      alias: mom_60_mean
      params: { field: mom_60, window: 60, agg: mean }
    - op: windowed
      alias: mom_60_std
      params: { field: mom_60, window: 60, agg: std }
    - op: arithmetic
      alias: mom_60_z
      params: { lhs: mom_60, rhs: mom_60_mean, op: '-' }
    - op: arithmetic
      alias: mom_60_z_scaled
      params: { lhs: mom_60_z, rhs: mom_60_std, op: '/' }
    # 对 neg_vol
    - op: windowed
      alias: neg_vol_mean
      params: { field: neg_vol, window: 60, agg: mean }
    - op: windowed
      alias: neg_vol_std
      params: { field: neg_vol, window: 60, agg: std }
    - op: arithmetic
      alias: neg_vol_z
      params: { lhs: neg_vol, rhs: neg_vol_mean, op: '-' }
    - op: arithmetic
      alias: neg_vol_z_scaled
      params: { lhs: neg_vol_z, rhs: neg_vol_std, op: '/' }
    # 对 ep
    - op: windowed
      alias: ep_mean
      params: { field: ep, window: 60, agg: mean }
    - op: windowed
      alias: ep_std
      params: { field: ep, window: 60, agg: std }
    - op: arithmetic
      alias: ep_z
      params: { lhs: ep, rhs: ep_mean, op: '-' }
    - op: arithmetic
      alias: ep_z_scaled
      params: { lhs: ep_z, rhs: ep_std, op: '/' }
    # 对 roe_weighted
    - op: windowed
      alias: roe_mean
      params: { field: roe_weighted, window: 60, agg: mean }
    - op: windowed
      alias: roe_std
      params: { field: roe_weighted, window: 60, agg: std }
    - op: arithmetic
      alias: roe_z
      params: { lhs: roe_weighted, rhs: roe_mean, op: '-' }
    - op: arithmetic
      alias: roe_z_scaled
      params: { lhs: roe_z, rhs: roe_std, op: '/' }
    # 对 net_profit_yoy
    - op: windowed
      alias: npy_mean
      params: { field: net_profit_yoy, window: 60, agg: mean }
    - op: windowed
      alias: npy_std
      params: { field: net_profit_yoy, window: 60, agg: std }
    - op: arithmetic
      alias: npy_z
      params: { lhs: net_profit_yoy, rhs: npy_mean, op: '-' }
    - op: arithmetic
      alias: npy_z_scaled
      params: { lhs: npy_z, rhs: npy_std, op: '/' }
    # 6. 等权求和得到综合得分
    - op: arithmetic
      alias: sum1
      params: { lhs: reversal_z_scaled, rhs: mom_60_z_scaled, op: '+' }
    - op: arithmetic
      alias: sum2
      params: { lhs: sum1, rhs: neg_vol_z_scaled, op: '+' }
    - op: arithmetic
      alias: sum3
      params: { lhs: sum2, rhs: ep_z_scaled, op: '+' }
    - op: arithmetic
      alias: sum4
      params: { lhs: sum3, rhs: roe_z_scaled, op: '+' }
    - op: arithmetic
      alias: sum5
      params: { lhs: sum4, rhs: npy_z_scaled, op: '+' }
    - op: arithmetic
      alias: score
      params: { lhs: sum5, rhs: 6, op: '/' }
  signals:
    - type: operator
      op: rank_top
      params: { field: score, ascending: false, top: 30 }
  weights:
    method: equal
"""

# ── 候选策略 B：候选 A + 风控（止损+最大回撤清仓）─────────────────────
# 核心假设：2022-2024 熊市中纯多头策略必然亏损，加入风控可在回撤超限时清仓避险。
# AGENTS.md："弱市下唯一可用风控是空仓+止损+最大回撤清仓"。
# 参数：stop_loss=0.15（单股亏 15% 止损）、max_drawdown_limit=0.3（组合回撤 30% 清仓）。
CANDIDATE_B = CANDIDATE_A.replace(
    "  weights:\n    method: equal\n",
    "  weights:\n    method: equal\n"
    "  risk_control:\n"
    "    max_position_per_stock: 0.1\n"
    "    stop_loss: 0.15\n"
    "    max_drawdown_limit: 0.3\n",
)


def fmt_pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def get_csi500_symbols() -> list[str]:
    """从 PostgreSQL 缓存获取 csi500 成分股（避免依赖 miniqmt 实时连接）。"""
    cache = DataCache()
    # 优先用空 date 取最新快照（成分股变化慢，近似可接受）
    symbols = cache.get_universe("中证500", "")
    if not symbols:
        symbols = cache.get_universe("csi500", "")
    if not symbols:
        raise RuntimeError("缓存中无 csi500/中证500 成分股数据")
    return symbols


def print_outcome(o, label: str) -> None:
    """打印单个回测结果。"""
    print(f"\n[{label}]")
    if not o.success:
        print(f"  ❌ 回测失败: {o.error}")
        print(f"  error_category: {o.error_category}")
        return
    print(f"  总收益率:   {fmt_pct(o.total_return)}")
    print(f"  年化收益率: {fmt_pct(o.annual_return)}")
    print(f"  夏普比率:   {o.sharpe_ratio:.4f}")
    print(f"  最大回撤:   {fmt_pct(o.max_drawdown)}")
    print(f"  波动率:     {fmt_pct(o.volatility)}")
    print(f"  calmar:     {o.calmar_ratio:.4f}")
    print(f"  sortino:    {o.sortino_ratio:.4f}")
    print(f"  胜率:       {fmt_pct(o.win_rate)}")
    print(f"  交易天数:   {o.trading_days}")
    print(f"  指标可信:   {'否' if o.metrics_unreliable else '是'}")


def print_comparison(
    baseline,
    candidate,
    candidate_label: str = "候选",
    train_start: str = "",
    train_end: str = "",
) -> None:
    """打印对比表格。

    Args:
        baseline: 基准 BacktestOutcome
        candidate: 候选 BacktestOutcome
        candidate_label: 候选策略标签（如 "候选A" / "候选B"），用于表头与结论
        train_start / train_end: 训练集窗口（用于表头展示）
    """
    print("\n" + "=" * 80)
    print(f"对比结论（训练集 {train_start} ~ {train_end}）")
    print("=" * 80)
    if not baseline.success or not candidate.success:
        print("  ⚠ 至少一个策略回测失败，无法对比")
        return

    print(
        f"{'指标':<16} {'基准(最佳)':>16} {candidate_label:>20} "
        f"{'差异':>16} {'结论':>8}"
    )
    print("-" * 80)

    # (名称, 基准值, 候选值, 是否百分比)
    rows = [
        ("夏普比率", baseline.sharpe_ratio, candidate.sharpe_ratio, False),
        ("总收益率", baseline.total_return, candidate.total_return, True),
        ("年化收益率", baseline.annual_return, candidate.annual_return, True),
        ("最大回撤", baseline.max_drawdown, candidate.max_drawdown, True),
        ("calmar", baseline.calmar_ratio, candidate.calmar_ratio, False),
        ("sortino", baseline.sortino_ratio, candidate.sortino_ratio, False),
        ("波动率", baseline.volatility, candidate.volatility, True),
    ]

    sharpe_improved = False
    for name, b, c, is_pct in rows:
        if is_pct:
            diff = (c - b) * 100
            b_str = f"{b * 100:.2f}%"
            c_str = f"{c * 100:.2f}%"
            d_str = f"{diff:+.2f}pp"
        else:
            diff = c - b
            b_str = f"{b:.4f}"
            c_str = f"{c:.4f}"
            d_str = f"{diff:+.4f}"
        if name == "夏普比率":
            sharpe_improved = diff > 0.05
        if name in ("最大回撤", "波动率"):
            ok = "✅" if diff < 0 else "❌"
        else:
            ok = "✅" if diff > 0 else "❌"
        print(f"  {name:<14} {b_str:>16} {c_str:>20} {d_str:>16} {ok:>6}")

    print()
    if sharpe_improved:
        print(f"  ✅ {candidate_label} 夏普比率显著提升（>0.05），可考虑作为新基准")
        print("  下一步：通过 HTR 合并门在测试集验证 OOS 表现")
    else:
        print(f"  ❌ {candidate_label} 夏普比率未显著提升（≤0.05），不替换基准")
        print("  下一步：尝试假设 B（流动性过滤）或假设 C（RSI 技术面）")


def main() -> None:
    max_workers = os.cpu_count() or 4
    if "--max-workers" in sys.argv:
        idx = sys.argv.index("--max-workers")
        max_workers = int(sys.argv[idx + 1])
    # 任务数 = 3（基准 + 候选 A + 候选 B），worker 数不超过任务数
    max_workers = min(max_workers, 3)

    config = AppConfig.from_env()
    # 严格遵循量化数据分割规范：仅用训练集（窗口从 config 派生，不硬编码）
    train_start = config.train_start_date
    train_end = config.train_end_date
    config.backtest_start_date = train_start
    config.backtest_end_date = train_end
    ctx = initialize_context(config)

    # 1. 获取股票池（从缓存，避免 miniqmt 依赖）
    print("=" * 80)
    print("多核并行回测对比")
    print("=" * 80)
    symbols = get_csi500_symbols()
    formatted_symbols = PandasToPolarsProvider.format_symbols(symbols)
    print(f"股票池: csi500, {len(formatted_symbols)} 只")
    print(f"回测窗口: {train_start} ~ {train_end}")
    print(f"并发 worker 数: {max_workers}")

    # 2. 读取基准策略 + 候选策略
    baseline_path = best_strategy_path()
    print(f"基准策略文件: {baseline_path}")
    if not Path(baseline_path).exists():
        print("❌ 最佳策略文件不存在，退出")
        return
    baseline_yaml = Path(baseline_path).read_text(encoding="utf-8")

    yamls = [
        (baseline_yaml, "基准: MultiCycleMomentumValueEarningsComposite"),
        (CANDIDATE_A, "候选A: ReversalMomentumHybrid"),
        (CANDIDATE_B, "候选B: ReversalMomentumHybrid+风控"),
    ]

    # 3. 并行回测（run_candidates：每个 YAML 独立解析风控参数与 warmup。
    #    候选 A 无 risk_control → 无风控裸跑；候选 B 自带 risk_control → 生效，
    #    A/B 对照语义自此真实成立，不再被基准风控参数覆盖）
    runner = ParallelRunner(max_workers=max_workers, data_provider=ctx.data_provider)
    print(f"\n开始并行回测（{len(yamls)} 策略, max_workers={max_workers}）...")
    t0 = time.time()
    outcomes = runner.run_candidates(
        strategy_yamls=[yaml_str for yaml_str, _ in yamls],
        start_date=train_start,
        end_date=train_end,
        symbols=formatted_symbols,
        benchmark_symbol="000300.SH",
    )
    t1 = time.time()
    print(f"回测完成: {t1 - t0:.1f}s")

    # 4. 打印结果
    for i, (_yaml_str, label) in enumerate(yamls):
        print_outcome(outcomes[i], label)

    # 5. 对比：候选 A vs 基准，候选 B vs 基准
    baseline_o = outcomes[0]
    candidate_a_o = outcomes[1]
    candidate_b_o = outcomes[2]

    print("\n" + "=" * 80)
    print("对比 1: 候选 A（反转+动量）vs 基准")
    print("=" * 80)
    print_comparison(
        baseline_o,
        candidate_a_o,
        candidate_label="候选A",
        train_start=train_start,
        train_end=train_end,
    )

    print("\n" + "=" * 80)
    print("对比 2: 候选 B（反转+动量+风控）vs 基准")
    print("=" * 80)
    print_comparison(
        baseline_o,
        candidate_b_o,
        candidate_label="候选B",
        train_start=train_start,
        train_end=train_end,
    )

    # 6. 选出最佳候选
    print("\n" + "=" * 80)
    print("最终结论")
    print("=" * 80)
    candidates = [
        ("候选A", candidate_a_o),
        ("候选B", candidate_b_o),
    ]
    best_candidate = None
    best_sharpe = baseline_o.sharpe_ratio if baseline_o.success else -999
    for name, o in candidates:
        if o.success and o.sharpe_ratio > best_sharpe + 0.05:
            best_sharpe = o.sharpe_ratio
            best_candidate = (name, o)
    if best_candidate:
        name, o = best_candidate
        print(f"  ✅ {name} 夏普 {o.sharpe_ratio:.4f} 显著超越基准（>0.05）")
        print(f"     总收益 {fmt_pct(o.total_return)}, 回撤 {fmt_pct(o.max_drawdown)}")
    else:
        print("  ❌ 无候选显著超越基准（夏普提升 ≤ 0.05）")
        print("     基准夏普: "
              f"{baseline_o.sharpe_ratio:.4f}" if baseline_o.success else "基准失败")
        for name, o in candidates:
            if o.success:
                print(f"     {name} 夏普: {o.sharpe_ratio:.4f}")


if __name__ == "__main__":
    main()
