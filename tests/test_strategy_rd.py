import pytest
from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph


def test_strategy_rd_subgraph():
    """测试策略研究子图 - Reflexion 模式"""
    subgraph = create_strategy_rd_subgraph()
    
    initial_state = {
        "query": "测试策略研究",
        "max_iterations": 1
    }
    
    result = subgraph.invoke(initial_state)
    
    assert "strategy" in result
    assert "strategy_code" in result
    assert "backtest_result" in result
    assert "reflection" in result
    assert "iteration" in result
    assert result["iteration"] == 1


def test_strategy_rd_reflection_loop():
    """测试 Reflexion 循环"""
    subgraph = create_strategy_rd_subgraph()
    
    initial_state = {
        "query": "测试策略研究",
        "max_iterations": 2
    }
    
    result = subgraph.invoke(initial_state)
    
    assert result.get("iteration", 0) <= 2
    assert "should_continue" in result or result.get("should_continue") is not None
