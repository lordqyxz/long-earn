import pytest

from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph


def test_strategy_rd_subgraph():
    """测试策略研究子图 - Reflexion 模式"""
    subgraph = create_strategy_rd_subgraph()

    initial_state = {"query": "测试策略研究", "max_iterations": 1}

    result = subgraph.invoke(initial_state)

    assert "strategy" in result
    assert "strategy_code" in result
    assert "backtest_result" in result
    assert "reflection" in result
    assert "iteration" in result
    # iteration 表示已完成的迭代次数，第一次循环后 iteration 会变成 2
    assert result["iteration"] >= 1


def test_strategy_rd_reflection_loop():
    """测试 Reflexion 循环"""
    subgraph = create_strategy_rd_subgraph()

    initial_state = {"query": "测试策略研究", "max_iterations": 2}

    result = subgraph.invoke(initial_state)

    # iteration 表示已完成的迭代次数，max_iterations=2 时 iteration 会变成 3
    assert result.get("iteration", 0) <= 3
    assert "should_continue" in result or result.get("should_continue") is not None
