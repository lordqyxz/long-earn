"""策略反思收益对比集成测试

运行完整 strategy_rd 子图（max_iterations=2），用 stream() 捕获中间状态：
- 初始回测（backtest 节点）→ 记录 baseline 指标
- 反思 → 优化 → 优化版回测（backtest_optimized 节点）→ 记录 optimized 指标
- 对比 total_return / sharpe_ratio / max_drawdown，验证反思是否真正提升收益

运行方式：uv run python scripts/test_reflection_roi.py
"""

import sys
import traceback

from dotenv import load_dotenv

load_dotenv()

from long_earn.config import AppConfig
from long_earn.context_init import initialize_context
from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph


def _extract_metrics(backtest_result: dict) -> dict:
    """从 backtest_result 提取关键指标，兼容扁平/嵌套结构。"""
    if not backtest_result or "error" in backtest_result:
        return {
            "error": backtest_result.get("error", "unknown") if backtest_result else "empty",
            "total_return": None,
            "sharpe_ratio": None,
            "max_drawdown": None,
        }
    return {
        "total_return": backtest_result.get("total_return"),
        "sharpe_ratio": backtest_result.get("sharpe_ratio"),
        "max_drawdown": backtest_result.get("max_drawdown"),
    }


def _format_metrics(label: str, metrics: dict) -> str:
    """格式化指标输出。"""
    if metrics.get("error"):
        return f"  {label}: ERROR - {metrics['error'][:80]}"
    return (
        f"  {label}:\n"
        f"    总收益率 = {metrics['total_return']}\n"
        f"    夏普比率 = {metrics['sharpe_ratio']}\n"
        f"    最大回撤 = {metrics['max_drawdown']}"
    )


def main():
    print("=" * 70)
    print("策略反思收益对比集成测试")
    print("=" * 70)

    # 1. 初始化上下文
    print("\n[1/5] 初始化运行时上下文...")
    try:
        config = AppConfig.from_env()
        config.max_iterations = 2  # 2 轮迭代：初始 + 优化
        ctx = initialize_context(config)
        print(f"  LLM: {config.llm_type}/{config.llm_model}")
        print(f"  迭代次数: {config.max_iterations}")
    except Exception as e:
        print(f"  初始化失败: {e}")
        traceback.print_exc()
        return 1

    # 2. 创建子图
    print("\n[2/5] 创建策略研发子图...")
    try:
        subgraph = create_strategy_rd_subgraph(ctx)
        print(f"  节点数: {len(subgraph.nodes)}")
    except Exception as e:
        print(f"  创建失败: {e}")
        traceback.print_exc()
        return 1

    # 3. 运行子图（stream 模式，捕获中间状态）
    print("\n[3/5] 运行策略研发子图（stream 模式）...")
    query = "研究一个基于利润增长的选股策略"
    print(f"  查询: {query}")

    baseline_metrics = None
    optimized_metrics = None
    final_state = None
    node_count = 0

    try:
        for chunk in subgraph.stream({"query": query}, {"recursion_limit": 100}):
            for node_name, state_update in chunk.items():
                node_count += 1
                print(f"  [{node_count:2d}] 节点: {node_name}")

                # 捕获初始回测结果（backtest 节点，非 backtest_optimized）
                if node_name == "backtest" and "backtest_result" in state_update:
                    baseline_metrics = _extract_metrics(state_update["backtest_result"])
                    print(f"       → 捕获初始回测结果")
                    if baseline_metrics.get("total_return") is not None:
                        print(f"         总收益率 = {baseline_metrics['total_return']}")

                # 捕获优化版回测结果
                if node_name == "backtest_optimized" and "backtest_result" in state_update:
                    optimized_metrics = _extract_metrics(state_update["backtest_result"])
                    print(f"       → 捕获优化版回测结果")
                    if optimized_metrics.get("total_return") is not None:
                        print(f"         总收益率 = {optimized_metrics['total_return']}")

                # 捕获反思结果
                if node_name == "reflection" and "reflection" in state_update:
                    reflection = state_update.get("reflection", "")
                    if reflection:
                        print(f"       反思摘要: {str(reflection)[:100]}...")

                # 捕获改进建议
                if node_name == "reflection" and "improvement_suggestions" in state_update:
                    suggestions = state_update.get("improvement_suggestions", [])
                    if suggestions:
                        print(f"       改进建议数: {len(suggestions)}")
                        for i, s in enumerate(suggestions[:3], 1):
                            print(f"         {i}. {str(s)[:80]}")

                final_state = state_update

    except Exception as e:
        print(f"  运行失败: {e}")
        traceback.print_exc()
        return 1

    # 4. 对比指标
    print("\n[4/5] 指标对比...")
    print("\n" + "-" * 70)
    if baseline_metrics:
        print(_format_metrics("初始策略 (baseline)", baseline_metrics))
    else:
        print("  初始策略: 未捕获到回测结果")

    print()
    if optimized_metrics:
        print(_format_metrics("优化策略 (optimized)", optimized_metrics))
    else:
        print("  优化策略: 未捕获到回测结果（可能 max_iterations < 2 或优化失败）")

    print()
    if baseline_metrics and optimized_metrics:
        print("  收益变化:")
        if (
            baseline_metrics.get("total_return") is not None
            and optimized_metrics.get("total_return") is not None
        ):
            delta_return = optimized_metrics["total_return"] - baseline_metrics["total_return"]
            arrow = "↑" if delta_return > 0 else ("↓" if delta_return < 0 else "→")
            print(f"    总收益率: {baseline_metrics['total_return']} → {optimized_metrics['total_return']} ({arrow} {delta_return:+.4f})")

        if (
            baseline_metrics.get("sharpe_ratio") is not None
            and optimized_metrics.get("sharpe_ratio") is not None
        ):
            delta_sharpe = optimized_metrics["sharpe_ratio"] - baseline_metrics["sharpe_ratio"]
            arrow = "↑" if delta_sharpe > 0 else ("↓" if delta_sharpe < 0 else "→")
            print(f"    夏普比率: {baseline_metrics['sharpe_ratio']} → {optimized_metrics['sharpe_ratio']} ({arrow} {delta_sharpe:+.4f})")

        if (
            baseline_metrics.get("max_drawdown") is not None
            and optimized_metrics.get("max_drawdown") is not None
        ):
            delta_dd = optimized_metrics["max_drawdown"] - baseline_metrics["max_drawdown"]
            # 回撤降低是好事
            arrow = "↓" if delta_dd < 0 else ("↑" if delta_dd > 0 else "→")
            good = "✓ 改善" if delta_dd < 0 else "✗ 恶化"
            print(f"    最大回撤: {baseline_metrics['max_drawdown']} → {optimized_metrics['max_drawdown']} ({arrow} {delta_dd:+.4f}) {good}")
    print("-" * 70)

    # 5. 结论
    print("\n[5/5] 结论...")
    if baseline_metrics and optimized_metrics:
        if (
            baseline_metrics.get("total_return") is not None
            and optimized_metrics.get("total_return") is not None
            and not baseline_metrics.get("error")
            and not optimized_metrics.get("error")
        ):
            delta_return = optimized_metrics["total_return"] - baseline_metrics["total_return"]
            if delta_return > 0:
                print(f"  ✓ 策略反思提升了收益率（+{delta_return:.4f}）")
                verdict = "PASS"
            elif delta_return == 0:
                print(f"  → 策略反思未改变收益率（±0）")
                verdict = "NEUTRAL"
            else:
                print(f"  ✗ 策略反思降低了收益率（{delta_return:.4f}）")
                verdict = "FAIL"
        else:
            print("  ? 回测存在错误，无法对比")
            verdict = "INCONCLUSIVE"
    else:
        print("  ? 未捕获到完整的回测对比数据")
        verdict = "INCONCLUSIVE"

    print("\n" + "=" * 70)
    print(f"测试结论: {verdict}")
    print("=" * 70)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
