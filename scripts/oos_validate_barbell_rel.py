#!/usr/bin/env python3
"""哑铃 relative/combined 轮次的 OOS 合并门验证（ADR-010 + ADR-015）。

对训练集网格冠军（com_rw60_m0，best_barbell_rel_strategy.yaml）与现任基准
（best_strategy.yaml）在测试集上并行跑 Walk-Forward（n_splits=3），按合并门
规则决策：

    1. S1 WalkForwardStabilityGate（ADR-015）：最差折 sharpe > -0.1、
       折间 std < 0.8、正折占比 ≥ 2/3 —— 上一轮 absolute W120 即死于本门
       （fold 0 2025Q1 -30.66%）
    2. 合并阈值：mean(oos_sharpe 候选) > mean(oos_sharpe 基准) + 0.05

铁律：测试集仅用于合并门决策，不用于参数调优；本轮只提交 1 个候选，
最小化测试集触碰。验证集（2026-03-25 ~ 2026-06-25）绝对不碰。

用法:
    uv run python scripts/oos_validate_barbell_rel.py
"""

from __future__ import annotations

import shutil
import sys
import time
from datetime import date
from pathlib import Path
from typing import cast

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from long_earn.core.stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

from loguru import logger as _loguru_logger  # noqa: E402

_loguru_logger.remove()
_loguru_logger.add(sys.stderr, level="ERROR")

from long_earn.backtest.data.polars_adapter import (  # noqa: E402
    PandasToPolarsProvider,
)
from long_earn.backtest.engine.parallel import ParallelRunner  # noqa: E402
from long_earn.config import AppConfig  # noqa: E402
from long_earn.context_init import initialize_context  # noqa: E402
from long_earn.core.storage import (  # noqa: E402
    best_strategy_path,
    get_data_dir,
)
from long_earn.strategy_optimization.overfit_gates import (  # noqa: E402
    WalkForwardStabilityGate,
)

# 门控基准与防守腿（哑铃策略面板必备，run_walk_forward 不会自动注入）
BENCHMARK = "000300.SH"
DEFENSIVE = "512890.SH"
MERGE_THRESHOLD = 0.05


def fmt_pct(x: float | None) -> str:
    return "N/A" if x is None else f"{x * 100:.2f}%"


def _fold_table(fold_results: list[dict[str, object]], label: str) -> None:
    """打印各折 test 指标表。"""
    print(f"\n  [{label}] 各折 test 指标:")
    for f in fold_results:
        fold_id = f.get("fold_id", "?")
        test = f.get("test", {})
        test_d = cast(dict[str, object], test)
        sharpe = test_d.get("sharpe_ratio")
        ret = test_d.get("total_return")
        dd = test_d.get("max_drawdown")
        s_str = f"{sharpe:.3f}" if isinstance(sharpe, float) else "ERR"
        r_str = fmt_pct(ret) if isinstance(ret, float) else "ERR"
        d_str = fmt_pct(dd) if isinstance(dd, float) else "ERR"
        print(
            f"    fold {fold_id}: sharpe={s_str}  ret={r_str}  max_dd={d_str}"
        )


def _mean_test_sharpe(fold_results: list[dict[str, object]]) -> float | None:
    """各折 test sharpe 均值（合并门判据）。"""
    sharpes: list[float] = []
    for f in fold_results:
        test = f.get("test", {})
        sharpe = cast(dict[str, object], test).get("sharpe_ratio")
        if isinstance(sharpe, float):
            sharpes.append(sharpe)
    return sum(sharpes) / len(sharpes) if sharpes else None


def main() -> None:
    config = AppConfig.from_env()
    test_start = config.test_start_date
    test_end = config.test_end_date

    baseline_path = Path(best_strategy_path())
    candidate_path = get_data_dir() / "best_barbell_rel_strategy.yaml"
    if not baseline_path.exists() or not candidate_path.exists():
        print("❌ 基准或候选策略文件缺失，退出")
        return
    baseline_yaml = baseline_path.read_text(encoding="utf-8")
    candidate_yaml = candidate_path.read_text(encoding="utf-8")

    ctx = initialize_context(config)
    assert ctx.data_provider is not None, "RuntimeContext 未提供 data_provider"

    print("=" * 90)
    print("哑铃 relative/combined 轮次 — OOS 合并门验证（Walk-Forward 3 折）")
    print("=" * 90)
    print(f"测试集: {test_start} ~ {test_end}（held-out，仅合并门触碰）")
    print(f"基准: {baseline_path.name}")
    print(f"候选: {candidate_path.name}")
    print(f"合并门: S1 稳定性门 且 mean(oos_sharpe) 差 > {MERGE_THRESHOLD}")

    # 股票池：候选 universe（main_board+gem）@ 测试集末，另注入 benchmark/防守腿
    symbols = ctx.data_provider.get_symbols("main_board+gem", test_end)
    formatted = PandasToPolarsProvider.format_symbols(symbols)
    for extra in (BENCHMARK, DEFENSIVE):
        if extra not in formatted:
            formatted.append(extra)
    print(f"股票池: main_board+gem {len(formatted)} 只（含 {BENCHMARK}/{DEFENSIVE}）")

    runner = ParallelRunner(max_workers=8, data_provider=ctx.data_provider)

    results: dict[str, dict[str, object]] = {}
    for label, yaml_str in (("基准", baseline_yaml), ("候选", candidate_yaml)):
        print(f"\n[{label}] Walk-Forward 回测中（n_splits=3）...")
        t0 = time.time()
        res = runner.run_walk_forward_parallel(
            strategy_yaml=yaml_str,
            start_date=test_start,
            end_date=test_end,
            symbols=formatted,
            n_splits=3,
            benchmark_symbol=BENCHMARK,
        )
        if "error" in res:
            print(f"  ❌ {label} Walk-Forward 失败: {res['error']}")
            return
        print(f"  完成: {time.time() - t0:.1f}s")
        results[label] = res

    base_res = results["基准"]
    cand_res = results["候选"]
    base_folds = cast(
        list[dict[str, object]], base_res.get("fold_results", [])
    )
    cand_folds = cast(
        list[dict[str, object]], cand_res.get("fold_results", [])
    )

    _fold_table(base_folds, "基准")
    _fold_table(cand_folds, "候选")

    base_sharpe = _mean_test_sharpe(base_folds)
    cand_sharpe = _mean_test_sharpe(cand_folds)
    if base_sharpe is None or cand_sharpe is None:
        print("❌ 存在无效折 sharpe，无法决策")
        return

    # S1 稳定性门（ADR-015）
    gate = WalkForwardStabilityGate()
    s1 = gate.evaluate(cand_folds)
    print("\n" + "=" * 90)
    print("合并门决策")
    print("=" * 90)
    print(f"  基准 mean(oos_sharpe): {base_sharpe:.4f}")
    print(f"  候选 mean(oos_sharpe): {cand_sharpe:.4f}")
    print(f"  差异: {cand_sharpe - base_sharpe:+.4f}（阈值 +{MERGE_THRESHOLD:.2f}）")
    print(f"  S1 稳定性门: {'✅ ' if s1.passed else '❌ '}{s1.reason}")

    if not s1.passed:
        print("\n  ❌ CONTINUE: 候选未通过 S1 稳定性门（上轮 absolute W120 同因出局）")
        print("  不替换基准；训练集内继续迭代")
        return
    if cand_sharpe - base_sharpe <= MERGE_THRESHOLD:
        print("\n  ❌ CONTINUE: 候选 OOS sharpe 未显著超越基准")
        print("  不替换基准；训练集内继续迭代")
        return

    print("\n  ✅ MERGE: 候选通过 S1 稳定性门且 OOS sharpe 显著超越基准")
    backup = baseline_path.with_suffix(
        f".yaml.bak-{date.today().isoformat()}"
    )
    shutil.copy2(baseline_path, backup)
    print(f"  旧基准已备份: {backup.name}")
    candidate_path.replace(baseline_path)
    print(f"  候选已提升为新基准: {baseline_path}")
    print("  （本轮结果为对外报告前状态；验证集最终评估另行一次性触碰）")


if __name__ == "__main__":
    main()
