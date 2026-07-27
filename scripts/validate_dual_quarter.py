"""对当前最佳策略做双季度前瞻验证。

Q1 2026 (2026-01-01 ~ 2026-03-31) + Q2 2026 (2026-04-01 ~ 2026-06-30)。
两个窗口收益都需 > 0 才算通过。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from long_earn.config import AppConfig  # noqa: E402
from long_earn.context_init import initialize_context  # noqa: E402
from long_earn.core.storage import best_strategy_path  # noqa: E402


def main() -> None:
    config = AppConfig.from_env()
    config.backtest_start_date = config.train_start_date
    config.backtest_end_date = config.train_end_date

    ctx = initialize_context(config)
    backtest = ctx.require_backtest()

    yaml_path = best_strategy_path()
    if not yaml_path.exists():
        print(f"未找到最佳策略: {yaml_path}")
        return
    strategy_yaml = yaml_path.read_text(encoding="utf-8")
    print("=" * 64)
    print("最佳策略 YAML:")
    print("-" * 64)
    print(strategy_yaml)
    print("=" * 64)

    quarters = [
        ("Q1 2026", "2026-01-01", "2026-03-31"),
        ("Q2 2026", "2026-04-01", "2026-06-30"),
    ]

    results: dict[str, dict] = {}
    for name, start, end in quarters:
        print()
        print(f"正在回测 {name} ({start} ~ {end})...")
        report = backtest.run(
            strategy_yaml=strategy_yaml,
            start_date=start,
            end_date=end,
        )
        if "error" in report:
            print(f"  {name} 回测失败: {report['error']}")
            results[name] = {"error": report["error"]}
            continue
        ret = float(report.get("total_return", -999.0))
        sharpe = float(report.get("sharpe_ratio", -999.0))
        drawdown = float(report.get("max_drawdown", -999.0))
        trade_count = int(report.get("trade_count", 0))
        trading_days = int(report.get("trading_days", 0))
        print(
            f"  {name}: return={ret:.4f}, sharpe={sharpe:.2f}, "
            f"drawdown={drawdown:.4f}, trades={trade_count}, days={trading_days}"
        )
        results[name] = {
            "return": ret,
            "sharpe": sharpe,
            "drawdown": drawdown,
            "trade_count": trade_count,
            "trading_days": trading_days,
        }

    print()
    print("=" * 64)
    print("双季度前瞻验证结果")
    print("=" * 64)
    q1 = results.get("Q1 2026", {})
    q2 = results.get("Q2 2026", {})
    q1_ret = q1.get("return", -999.0)
    q2_ret = q2.get("return", -999.0)
    threshold = 0.0
    q1_pass = q1_ret > threshold
    q2_pass = q2_ret > threshold
    passed = q1_pass and q2_pass

    print(f"  Q1 2026: return={q1_ret:.4f}, sharpe={q1.get('sharpe', 0):.2f}")
    print(f"  Q2 2026: return={q2_ret:.4f}, sharpe={q2.get('sharpe', 0):.2f}")
    print(f"  收益阈值: {threshold:.4f}")
    print("-" * 64)
    if passed:
        print(
            f"  ✅ 双季度验证通过：Q1={q1_ret:.4f}, Q2={q2_ret:.4f} "
            f"均 > {threshold:.4f}"
        )
    else:
        failed = []
        if not q1_pass:
            failed.append(f"Q1={q1_ret:.4f}")
        if not q2_pass:
            failed.append(f"Q2={q2_ret:.4f}")
        print(
            f"  ❌ 双季度验证未通过：{', '.join(failed)} 未达阈值 "
            f"{threshold:.4f}"
        )
    print("=" * 64)

    # 保存结果
    output = {
        "strategy_yaml": strategy_yaml,
        "q1_2026": q1,
        "q2_2026": q2,
        "threshold": threshold,
        "passed": passed,
    }
    out_path = Path("D:/dev/long-earn-data/dual_quarter_validation.json")
    out_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"结果已保存到: {out_path}")


if __name__ == "__main__":
    main()
