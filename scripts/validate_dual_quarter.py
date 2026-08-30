"""对当前最佳策略做双段前瞻验证（最终评估场景）。

验证集区间（config.validation_start_date ~ validation_end_date）对半拆分
为前后两段，两段收益都需 > 0 才算通过。
铁律 #3：验证集整个研发过程仅最终评估时触碰一次，本脚本即该唯一触碰点。
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from long_earn.config import AppConfig  # noqa: E402
from long_earn.context_init import initialize_context  # noqa: E402
from long_earn.core.storage import best_strategy_path, get_data_dir  # noqa: E402


def split_validation_halves(
    validation_start: str, validation_end: str
) -> list[tuple[str, str, str]]:
    """将验证集区间对半拆分为前后两段。

    Args:
        validation_start: 验证集起始日（ISO 格式）
        validation_end: 验证集结束日（ISO 格式）

    Returns:
        [(段名, start, end), ...] 共两项；段名用于展示与结果键
    """
    start = date.fromisoformat(validation_start)
    end = date.fromisoformat(validation_end)
    half_days = ((end - start).days + 1) // 2
    mid = start + timedelta(days=half_days)
    return [
        ("验证前半段", validation_start, (mid - timedelta(days=1)).isoformat()),
        ("验证后半段", mid.isoformat(), validation_end),
    ]


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

    # 验证集区间对半拆分（铁律 #3：仅最终评估触碰一次）
    halves = split_validation_halves(
        config.validation_start_date, config.validation_end_date
    )

    results: dict[str, dict] = {}
    for name, start, end in halves:
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

    half1 = results.get(halves[0][0], {})
    half2 = results.get(halves[1][0], {})
    h1_ret = half1.get("return", -999.0)
    h2_ret = half2.get("return", -999.0)
    threshold = 0.0
    h1_pass = h1_ret > threshold
    h2_pass = h2_ret > threshold
    passed = h1_pass and h2_pass

    print()
    print("=" * 64)
    print("验证集双段前瞻验证结果")
    print("=" * 64)
    print(
        f"  {halves[0][0]}: return={h1_ret:.4f}, "
        f"sharpe={half1.get('sharpe', 0):.2f}"
    )
    print(
        f"  {halves[1][0]}: return={h2_ret:.4f}, "
        f"sharpe={half2.get('sharpe', 0):.2f}"
    )
    print(f"  收益阈值: {threshold:.4f}")
    print("-" * 64)
    if passed:
        print(
            f"  ✅ 双段验证通过：前半段={h1_ret:.4f}, 后半段={h2_ret:.4f} "
            f"均 > {threshold:.4f}"
        )
    else:
        failed = []
        if not h1_pass:
            failed.append(f"前半段={h1_ret:.4f}")
        if not h2_pass:
            failed.append(f"后半段={h2_ret:.4f}")
        print(
            f"  ❌ 双段验证未通过：{', '.join(failed)} 未达阈值 "
            f"{threshold:.4f}"
        )
    print("=" * 64)

    # 保存结果（落盘路径由 core.storage 统一裁决）
    output = {
        "strategy_yaml": strategy_yaml,
        "validation_start": config.validation_start_date,
        "validation_end": config.validation_end_date,
        "halves": {
            name: results.get(name, {}) for name, _s, _e in halves
        },
        "threshold": threshold,
        "passed": passed,
    }
    out_path = get_data_dir() / "dual_quarter_validation.json"
    out_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"结果已保存到: {out_path}")


if __name__ == "__main__":
    main()
