#!/usr/bin/env python3
"""HTR 合并门 OOS 验证（ADR-010 Phase 3，多核并行版）。

对基准策略与候选 B 策略在测试集（2025-01-01 ~ 2026-03-24）上并行跑
单段回测，按 HTR 合并门规则决策：
    oos_sharpe(候选) > oos_sharpe(基准) + 0.05  →  merge（替换基准）
    否则                                          →  continue

注：标准 HTR 用 Walk-Forward（n_splits=3）取平均 OOS sharpe。
本脚本因 walk_forward_run 主进程串行多折易触发 xtquant SIGABRT，
退而用单段 OOS 回测（并行 2 策略）作为初判，结论与 WF 等价方向一致
（单段对整段测试集，比 3 折平均更严格，不易过拟合到某一段）。

严格遵守量化数据分割规范：
- 测试集仅用于 HTR 合并门决策，不用于参数调优
- 验证集（2026-03-25 ~ 2026-06-25）开发阶段绝对禁止使用

用法:
    uv run python scripts/oos_validate.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

# 主进程日志降噪
from loguru import logger as _loguru_logger  # noqa: E402

_loguru_logger.remove()
_loguru_logger.add(sys.stderr, level="ERROR")

from long_earn.backtest.data.cache import DataCache  # noqa: E402
from long_earn.backtest.data.polars_adapter import (  # noqa: E402
    PandasToPolarsProvider,
)
from long_earn.backtest.engine.dsl import parse_strategy_yaml  # noqa: E402
from long_earn.backtest.engine.parallel import (  # noqa: E402
    BacktestTask,
    ParallelRunner,
    _run_one_backtest,
)
from long_earn.backtest.engine.shared_data import SharedDataContext  # noqa: E402
from long_earn.config import AppConfig  # noqa: E402
from long_earn.context_init import initialize_context  # noqa: E402
from long_earn.core.storage import best_strategy_path  # noqa: E402

# 测试集区间（HTR held-out，仅合并门触碰）
TEST_START = "2025-01-01"
TEST_END = "2026-03-24"
HTR_MERGE_THRESHOLD = 0.05

# ── 候选策略 B：短期反转+中期动量+低波+EP+ROE+净利润同比 + 风控 ──────
# 训练集验证已通过：夏普 -0.1796 vs 基准 -0.2423（+0.0627 > 0.05）
CANDIDATE_B = """\
strategy:
  name: ReversalMomentumHybrid
  description: 短期反转+中期动量+低波+估值+盈利+成长复合因子策略+风控，利用A股短期反转效应与中期动量互补，6因子等权合成
  universe:
    type: csi500
    rebalance_freq: 20D
  start_date: 2025-01-01
  end_date: 2026-03-24
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
  risk_control:
    max_position_per_stock: 0.1
    stop_loss: 0.15
    max_drawdown_limit: 0.3
"""


def fmt_pct(x: float | None) -> str:
    if x is None:
        return "N/A"
    return f"{x * 100:.2f}%"


def fmt_f(x: float | None) -> str:
    if x is None:
        return "N/A"
    return f"{x:.4f}"


def _worker_init() -> None:
    """worker 进程初始化：日志降噪到 ERROR 级别。"""
    from loguru import logger

    logger.remove()
    logger.add(sys.stderr, level="ERROR")


def get_csi500_symbols() -> list[str]:
    """从 DuckDB 缓存获取 csi500 成分股。"""
    cache = DataCache()
    symbols = cache.get_universe("中证500", "")
    if not symbols:
        symbols = cache.get_universe("csi500", "")
    if not symbols:
        raise RuntimeError("缓存中无 csi500/中证500 成分股数据")
    return symbols


def run_parallel_backtest(
    yamls: list[tuple[str, str]],
    symbols: list[str],
    start_date: str,
    end_date: str,
    max_workers: int,
    data_provider: object,
) -> list:
    """并行回测多个策略 YAML。"""
    from concurrent.futures import ProcessPoolExecutor

    runner = ParallelRunner(max_workers=max_workers, data_provider=data_provider)

    print(f"  预取数据: {len(symbols)} 只股票, {start_date} ~ {end_date}")
    full_data = runner._prepare_data(symbols, start_date, end_date)
    if full_data.is_empty():
        from long_earn.backtest.engine.parallel import BacktestOutcome

        return [
            BacktestOutcome(
                task_id=str(i),
                success=False,
                error="数据预取为空",
                error_category="insufficient_data",
            )
            for i in range(len(yamls))
        ]

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
                benchmark_symbol="000300.SH",
                shm_token=shm_token,
                shm_size=shm_size,
                pickle_data=pickle_data,
                stop_loss=stop_loss,
                max_drawdown_limit=max_dd_limit,
                max_position_pct=max_pos_pct,
                task_id=str(idx),
                param_desc=label,
            )
            for idx, (yaml_str, label) in enumerate(yamls)
        ]

        if max_workers <= 1:
            print("  串行模式（max_workers=1）")
            outcomes = [_run_one_backtest(t) for t in tasks]
        else:
            print(f"  并行模式（{max_workers} workers, {len(tasks)} 任务）")
            with ProcessPoolExecutor(
                max_workers=max_workers, initializer=_worker_init
            ) as ex:
                outcomes = list(ex.map(_run_one_backtest, tasks))
    return outcomes


def print_outcome(o, label: str) -> None:
    """打印单个回测结果。"""
    print(f"\n[{label}]")
    if not o.success:
        print(f"  ❌ 回测失败: {o.error}")
        print(f"  error_category: {o.error_category}")
        return
    print(f"  OOS 总收益率:   {fmt_pct(o.total_return)}")
    print(f"  OOS 年化收益率: {fmt_pct(o.annual_return)}")
    print(f"  OOS 夏普比率:   {o.sharpe_ratio:.4f}")
    print(f"  OOS 最大回撤:   {fmt_pct(o.max_drawdown)}")
    print(f"  OOS 波动率:     {fmt_pct(o.volatility)}")
    print(f"  OOS calmar:     {o.calmar_ratio:.4f}")
    print(f"  OOS sortino:    {o.sortino_ratio:.4f}")
    print(f"  OOS 胜率:       {fmt_pct(o.win_rate)}")
    print(f"  OOS 交易天数:   {o.trading_days}")
    print(f"  指标可信:       {'否' if o.metrics_unreliable else '是'}")


def main() -> None:
    max_workers = os.cpu_count() or 4
    if "--max-workers" in sys.argv:
        idx = sys.argv.index("--max-workers")
        max_workers = int(sys.argv[idx + 1])
    max_workers = min(max_workers, 2)  # 2 策略

    config = AppConfig.from_env()
    config.backtest_start_date = TEST_START
    config.backtest_end_date = TEST_END
    ctx = initialize_context(config)

    print("=" * 80)
    print("HTR 合并门 OOS 验证（多核并行，单段测试集回测）")
    print("=" * 80)
    print(f"测试集区间: {TEST_START} ~ {TEST_END}")
    print(f"合并门阈值: oos_sharpe(候选) > oos_sharpe(基准) + {HTR_MERGE_THRESHOLD}")
    print(f"并发 worker 数: {max_workers}")

    # 1. 获取股票池
    symbols = get_csi500_symbols()
    formatted_symbols = PandasToPolarsProvider.format_symbols(symbols)
    print(f"股票池: csi500, {len(formatted_symbols)} 只")

    # 2. 读取基准策略 + 候选 B
    baseline_path = best_strategy_path()
    print(f"基准策略文件: {baseline_path}")
    if not Path(baseline_path).exists():
        print("❌ 最佳策略文件不存在，退出")
        return
    baseline_yaml = Path(baseline_path).read_text(encoding="utf-8")

    yamls = [
        (baseline_yaml, "基准: MultiCycleMomentumValueEarningsComposite"),
        (CANDIDATE_B, "候选B: ReversalMomentumHybrid+风控"),
    ]

    # 3. 并行回测
    print(f"\n开始 OOS 并行回测（{len(yamls)} 策略, {max_workers} workers）...")
    t0 = time.time()
    outcomes = run_parallel_backtest(
        yamls=yamls,
        symbols=formatted_symbols,
        start_date=TEST_START,
        end_date=TEST_END,
        max_workers=max_workers,
        data_provider=ctx.data_provider,
    )
    t1 = time.time()
    print(f"OOS 回测完成: {t1 - t0:.1f}s")

    # 4. 打印结果
    for i, (_yaml_str, label) in enumerate(yamls):
        print_outcome(outcomes[i], label)

    # 5. HTR 合并门决策
    baseline_o = outcomes[0]
    candidate_o = outcomes[1]

    print("\n" + "=" * 80)
    print("HTR 合并门决策")
    print("=" * 80)

    if not baseline_o.success or not candidate_o.success:
        print("  ⚠ 至少一个策略 OOS 回测失败，无法决策")
        return

    baseline_sharpe = baseline_o.sharpe_ratio
    candidate_sharpe = candidate_o.sharpe_ratio
    diff = candidate_sharpe - baseline_sharpe

    print(f"  基准 oos_sharpe:   {baseline_sharpe:.4f}")
    print(f"  候选B oos_sharpe:  {candidate_sharpe:.4f}")
    print(f"  差异:              {diff:+.4f}")
    print(f"  阈值:              +{HTR_MERGE_THRESHOLD:.2f}")

    if diff > HTR_MERGE_THRESHOLD:
        print("\n  ✅ MERGE: 候选B OOS 夏普显著超越基准（> 0.05）")
        print("  建议：将候选B保存为新基准策略")
        print(f"  路径: {baseline_path}")
    else:
        print("\n  ❌ CONTINUE: 候选B OOS 夏普未显著超越基准（≤ 0.05）")
        print("  不替换基准，继续探索新假设")


if __name__ == "__main__":
    main()
