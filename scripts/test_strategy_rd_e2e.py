"""策略研发子图端到端测试脚本

直接调用 strategy_rd 子图，验证完整流程能否正常运行。
运行方式：uv run python scripts/test_strategy_rd_e2e.py
"""

import sys
import traceback

from dotenv import load_dotenv

load_dotenv()

from long_earn.config import AppConfig, RuntimeContext
from long_earn.context_init import initialize_context
from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph


def test_strategy_rd_subgraph():
    """测试策略研发子图端到端流程"""
    print("=" * 60)
    print("策略研发子图端到端测试")
    print("=" * 60)

    # 1. 初始化上下文
    print("\n[1/4] 初始化运行时上下文...")
    try:
        config = AppConfig.from_env()
        config.max_iterations = 1  # 限制迭代次数，加快测试
        ctx = initialize_context(config)
        print(f"  LLM: {config.llm_type}/{config.llm_model}")
        print(f"  迭代次数: {config.max_iterations}")
    except Exception as e:
        print(f"  失败: {e}")
        traceback.print_exc()
        return False

    # 2. 创建子图
    print("\n[2/4] 创建策略研发子图...")
    try:
        subgraph = create_strategy_rd_subgraph(ctx)
        nodes = list(subgraph.nodes.keys())
        print(f"  节点: {nodes}")
    except Exception as e:
        print(f"  失败: {e}")
        traceback.print_exc()
        return False

    # 3. 运行子图
    print("\n[3/4] 运行策略研发子图...")
    query = "研究一个基于利润增长的选股策略"
    print(f"  查询: {query}")

    try:
        result = subgraph.invoke({"query": query})
        print("\n  === 运行结果 ===")
        for key, value in result.items():
            if key == "backtest_result" and isinstance(value, dict):
                if "error" in value:
                    print(f"  {key}: ERROR - {value['error'][:100]}")
                else:
                    print(f"  {key}: total_return={value.get('total_return')}, "
                          f"sharpe={value.get('sharpe_ratio')}, "
                          f"max_drawdown={value.get('max_drawdown')}")
            elif key == "strategy" and isinstance(value, dict):
                desc = value.get("description", "")
                print(f"  {key}: {desc[:100]}...")
            elif key == "strategy_yaml" and isinstance(value, str):
                print(f"  {key}: {value[:150]}...")
            elif key == "reflection" and isinstance(value, str):
                print(f"  {key}: {value[:100]}...")
            elif isinstance(value, (str, int, float, bool)):
                print(f"  {key}: {value}")
            elif isinstance(value, list):
                print(f"  {key}: [{len(value)} items]")
            elif isinstance(value, dict):
                print(f"  {key}: {list(value.keys())}")
            else:
                print(f"  {key}: {type(value).__name__}")
    except Exception as e:
        print(f"  运行失败: {e}")
        traceback.print_exc()
        return False

    # 4. 验证结果
    print("\n[4/4] 验证结果...")
    success = True
    checks = [
        ("strategy", lambda r: r.get("strategy") is not None),
        ("strategy_yaml", lambda r: bool(r.get("strategy_yaml"))),
        ("backtest_result", lambda r: r.get("backtest_result") is not None),
        ("reflection", lambda r: bool(r.get("reflection"))),
        ("experience_saved", lambda r: r.get("experience_saved") is not None),
    ]
    for name, check in checks:
        ok = check(result)
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {name}")
        if not ok:
            success = False

    print("\n" + "=" * 60)
    print(f"测试结果: {'通过' if success else '失败'}")
    print("=" * 60)
    return success


if __name__ == "__main__":
    ok = test_strategy_rd_subgraph()
    sys.exit(0 if ok else 1)
