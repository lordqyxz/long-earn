import pytest
from long_earn.strategy_rd.agents.strategy_develop_agent import StrategyDevelopAgent


def test_strategy_develop_agent():
    """测试策略开发子图"""
    develop_agent = StrategyDevelopAgent()
    strategy = {"name": "测试策略", "description": "这是一个测试策略"}
    code = develop_agent.develop_strategy(strategy)
    assert code is not None
    assert "class CustomStrategy" in code
    assert "def generate_signals" in code
