#!/usr/bin/env python3
"""用 LangGraph checkpoint 断点调试策略研发子图。

在关键节点（reflection/gap_detector/operator_dev/optimize）前暂停，
检查 state 确认算子路径 YAML 生成 + 家族切换打分逻辑，无需从头跑完整循环。

用法:
    uv run python scripts/debug_checkpoint.py
    uv run python scripts/debug_checkpoint.py --no-interrupt  # 不断点直接跑完
"""

from __future__ import annotations

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from loguru import logger  # noqa: E402

logger.remove()
logger.add(sys.stderr, level="WARNING")

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from long_earn.config import AppConfig  # noqa: E402
from long_earn.context_init import initialize_context  # noqa: E402
from long_earn.strategy_rd.subgraph import (  # noqa: E402
    create_strategy_rd_subgraph,
)

# 关键节点 — 在这些节点执行前暂停，可检查 state
INTERRUPT_NODES = ["reflection", "gap_detector", "operator_dev", "optimize"]

IDEA = "研究一个结合动量、波动率与ROE的选股策略，用算子路径实现，要求近六个月收益率最大化"


def _print_state_snapshot(state: dict, title: str) -> None:
    """打印 state 关键字段。"""
    print(f"\n{'─' * 70}")
    print(f"📋 {title}")
    print("─" * 70)

    strategy_yaml = (
        state.get("strategy_yaml", "") or state.get("optimized_strategy_yaml", "") or ""
    )
    if strategy_yaml:
        has_op = "operator_factors" in strategy_yaml
        print(f"  策略 YAML (前 300 字符):")
        print(f"  {strategy_yaml[:300]}")
        print(f"  含 operator_factors: {has_op}")

    bt = state.get("backtest_result", {}) or {}
    if bt:
        print(
            f"  回测: return={bt.get('total_return')}, "
            f"sharpe={bt.get('sharpe_ratio')}, "
            f"unreliable={bt.get('metrics_unreliable', 'N/A')}"
        )

    direction = state.get("selected_direction", "")
    if direction:
        print(f"  ToT 选定方向: {direction}")

    gaps = state.get("operator_gaps", []) or []
    if gaps:
        print(f"  算子缺口: {[g.get('name') for g in gaps]}")

    registered = state.get("registered_operators", []) or []
    if registered:
        print(f"  新注册算子: {registered}")

    history_ret = state.get("history_return", 0.0)
    if history_ret:
        print(f"  history_return: {history_ret:.4f}")


def main() -> None:
    use_interrupt = "--no-interrupt" not in sys.argv

    config = AppConfig.from_env()
    # 用短窗口加速调试（6 个月训练集）
    config.train_start_date = "2025-01-01"
    config.test_end_date = "2025-09-01"
    config.validation_start_date = "2025-09-02"
    config.validation_end_date = "2026-01-31"
    config.backtest_start_date = "2025-01-01"
    config.backtest_end_date = "2025-09-01"
    config.max_iterations = 1
    ctx = initialize_context(config)

    checkpointer = MemorySaver()
    interrupt = INTERRUPT_NODES if use_interrupt else None

    print("=" * 70)
    print("checkpoint 断点调试：算子路径 + 家族切换")
    print("=" * 70)
    print(f"  idea: {IDEA[:60]}...")
    print(f"  回测窗口: {config.backtest_start_date} ~ {config.backtest_end_date}")
    print(f"  interrupt 节点: {interrupt or '无（直接跑完）'}")
    print(f"  注入 history_return: -0.30（模拟动量长期亏损）")

    subgraph = create_strategy_rd_subgraph(
        ctx,
        checkpointer=checkpointer,
        interrupt_before=interrupt,
    )

    thread_config = {"configurable": {"thread_id": "debug-1"}}

    print("\n[启动] 第一次 invoke（跑到第一个断点或完成）...")
    result = subgraph.invoke(
        {"query": IDEA, "history_return": -0.30},
        config=thread_config,
    )

    # 如果有 interrupt，检查每个断点
    if use_interrupt:
        for _step in range(10):  # 最多 10 个断点
            # 获取当前状态快照
            snapshot = subgraph.get_state(thread_config)
            if snapshot.next:
                next_node = snapshot.next[0]
                print(f"\n[断点] 暂停在节点: {next_node}")
                _print_state_snapshot(snapshot.values, f"执行 {next_node} 前")

                # 继续执行到下一个断点
                result = subgraph.invoke(None, config=thread_config)
            else:
                print("\n[完成] 子图执行结束")
                break
    else:
        _print_state_snapshot(result, "最终 state")

    print("\n" + "=" * 70)
    print("调试结论")
    print("=" * 70)
    final_strategy = (
        result.get("strategy_yaml", "")
        or result.get("optimized_strategy_yaml", "")
        or ""
    )
    has_op = "operator_factors" in final_strategy
    direction = result.get("selected_direction", "")

    print(f"  ✅ 算子路径生效（operator_factors）: {has_op}")
    print(f"  ✅ ToT 选定方向: {direction}")
    print(f"  ✅ 家族切换分支被选中: {direction == '策略家族切换'}")

    gaps = result.get("operator_gaps", []) or []
    registered = result.get("registered_operators", []) or []
    print(f"  ✅ 算子缺口检测: {len(gaps)} 个")
    print(f"  ✅ 新算子注册: {registered}")

    bt = result.get("backtest_result", {}) or {}
    unreliable = bt.get("metrics_unreliable", True)
    trade_count = bt.get("strategy_diagnostics", {}).get("trade_count", 0) if bt else 0
    print(f"  ✅ 回测非退化: {not unreliable and trade_count > 0} (trade_count={trade_count})")


if __name__ == "__main__":
    main()