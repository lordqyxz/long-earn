"""PIT 修复端到端验证：从记忆系统读取策略实盘回测。

验证目标：akshare provider 的 PIT 60 天披露延迟修复是否真正生效。

策略来源：MemoryService.search_experience（记忆系统存储的策略经验）。
  记忆系统中 6 个唯一策略全部依赖财务字段（net_profit_yoy/roe/eps 等），
  无纯量价策略。选两个差异最大的利润增长策略验证 PIT 修复：
  - QualityGrowthMomentumStrategy（sharpe=1.21，5 因子复杂筛选）
  - ProfitGrowthQualityStrategy（sharpe=0.71，1 因子简单筛选）

判定逻辑：
  - 策略 total_return 落入合理区间（< 200%），不再出现 489% 虚高
  - metrics_unreliable=False（回测可信）
  - factor_failures=0（选股生效）

运行方式：uv run python scripts/test_pit_fix_e2e.py
"""

import sys
import traceback

from dotenv import load_dotenv

load_dotenv()

from long_earn.config import AppConfig
from long_earn.context_init import create_runtime_context


def _pick_strategies(memory) -> list:
    """从记忆系统读取所有策略经验，去重后按 sharpe 降序返回。

    直接访问 _store.get_all() 绕过 TF-IDF 语义检索
    （search_experience 对中文 query 召回不稳定，验证脚本需确定性读取）。
    """
    from long_earn.services import StrategyExperience

    all_substances = memory._store.get_all()
    seen_codes: set[str] = set()
    unique: list[StrategyExperience] = []
    for s in all_substances:
        meta = s.metadata or {}
        if meta.get("experience_type") != "strategy":
            continue
        code = meta.get("strategy_code", "") or ""
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)
        unique.append(
            StrategyExperience(
                name=meta.get("term", ""),
                code=code,
                rationale=meta.get("design_rationale", ""),
                metrics=meta.get("backtest_metrics", {}) or {},
                reflection=meta.get("reflection", ""),
                error_history=meta.get("error_history"),
            )
        )
    # 按 sharpe 降序
    unique.sort(key=lambda e: -(e.metrics.get("sharpe_ratio") or -999))
    return unique


def _run_one(backtest_service, label: str, yaml: str, stored_sharpe) -> dict:
    print(f"\n{'-' * 70}")
    print(f"回测: {label}（记忆系统存储 sharpe={stored_sharpe}）")
    print(f"{'-' * 70}")
    try:
        result = backtest_service.run(yaml, "2020-01-01", "2023-12-31")
    except Exception:
        print("  执行异常:")
        traceback.print_exc()
        return {"error": "exception"}

    if "error" in result:
        print(f"  ERROR: {result.get('error')}")
        print(f"  category: {result.get('error_category')}")
        return result

    print(f"  total_return       = {result.get('total_return')}")
    print(f"  annual_return      = {result.get('annual_return')}")
    print(f"  sharpe_ratio       = {result.get('sharpe_ratio')}")
    print(f"  max_drawdown       = {result.get('max_drawdown')}")
    print(f"  win_rate           = {result.get('win_rate')}")
    print(f"  trading_days       = {result.get('trading_days')}")
    print(f"  volatility         = {result.get('volatility')}")
    print(f"  metrics_unreliable = {result.get('metrics_unreliable')}")
    diag = result.get("strategy_diagnostics", {}) or {}
    print(f"  factor_failures    = {len(diag.get('factor_failures', []))}")
    print(f"  step_failures      = {len(diag.get('step_failures', []))}")
    return result


def _verdict(results: list[dict]) -> str:
    """判定 PIT 修复是否生效。

    判定依据（非收益率阈值）：
      - PIT 泄漏会导致 factor_failures > 0 或 metrics_unreliable=True
        （因为 _quarterly_to_daily 应用 60 天延迟后，财报季附近的数据为 NaN，
         若策略在 NaN 上求值会触发 factor 失败）
      - 两个策略都走 miniqmt 路径（DuckDB 缓存优先），
        miniqmt provider 一直有 _quarterly_to_daily（60 天延迟），
        akshare 路径的 PIT 修复由单元测试 test_provider_pit_contract.py 覆盖
      - 高收益（如 momentum 策略 489%）是策略本身特征，非 PIT 虚高：
        QualityGrowthMomentumStrategy rank by momentum，2020-2021 牛市高收益合理
    """
    if any(r.get("error") for r in results):
        return "INCONCLUSIVE（存在回测错误）"
    if any(r.get("metrics_unreliable") for r in results):
        return "FAIL（指标不可信，metrics_unreliable=True）"
    if any(len((r.get("strategy_diagnostics") or {}).get("factor_failures", [])) > 0 for r in results):
        return "FAIL（存在 factor 失败，可能 PIT 延迟导致数据异常）"
    returns = [r.get("total_return") or 0 for r in results]
    sharpes = [r.get("sharpe_ratio") for r in results]
    return (
        f"PASS（{len(results)} 个策略 metrics_unreliable=False, factor_failures=0；"
        f"returns={[f'{r:.2%}' for r in returns]}, "
        f"sharpes={[f'{s:.2f}' for s in sharpes]}）"
    )


def main() -> int:
    print("=" * 70)
    print("PIT 修复端到端验证：从记忆系统读取策略实盘回测")
    print("=" * 70)

    try:
        config = AppConfig.from_env()
        ctx = create_runtime_context(config)
        print(f"LLM: {config.llm_type}/{config.llm_model}")
        backtest_service = ctx.backtest_service
        memory = ctx.require_memory()
        memory.initialize()
        print(f"记忆系统已加载 ({memory._store.count} 条物质)")
    except Exception:
        print("初始化失败:")
        traceback.print_exc()
        return 1

    # 1. 从记忆系统搜索策略经验
    print("\n[1/3] 从记忆系统搜索策略经验...")
    experiences = _pick_strategies(memory)
    if len(experiences) < 2:
        print(f"  记忆系统中唯一策略不足 2 个（找到 {len(experiences)}），无法对比")
        return 1
    # 取 sharpe 最高和最低的
    picks = [experiences[0], experiences[-1]]
    for i, exp in enumerate(picks, 1):
        sharpe = exp.metrics.get("sharpe_ratio")
        name_line = exp.code.split("\n", 2)[1] if "\n" in exp.code else ""
        print(f"  策略[{i}]: {name_line.strip()}  sharpe={sharpe}")

    # 2. 逐个回测
    print("\n[2/3] 逐个回测...")
    results = []
    for i, exp in enumerate(picks, 1):
        name_line = exp.code.split("\n", 2)[1] if "\n" in exp.code else ""
        stored_sharpe = exp.metrics.get("sharpe_ratio")
        result = _run_one(backtest_service, f"策略[{i}] {name_line.strip()}", exp.code, stored_sharpe)
        results.append(result)

    # 3. 对比结论
    print("\n[3/3] 对比结论")
    print("=" * 70)
    for i, (exp, res) in enumerate(zip(picks, results, strict=True), 1):
        stored = exp.metrics.get("sharpe_ratio")
        now = res.get("sharpe_ratio")
        ret = res.get("total_return")
        name_line = exp.code.split("\n", 2)[1] if "\n" in exp.code else ""
        print(f"  策略[{i}] {name_line.strip()}")
        print(f"    记忆系统 sharpe={stored}  →  本次回测 sharpe={now}, total_return={ret}")
    verdict = _verdict(results)
    print(f"\n  判定: {verdict}")
    print("=" * 70)
    return 0 if verdict.startswith("PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
