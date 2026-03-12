import pytest

from long_earn.agent import create_main_agent


def test_main_agent():
    """测试主图"""
    # 创建主图
    agent = create_main_agent()

    # 测试策略查询
    result = agent.invoke({"user_query": "测试策略"})
    assert "summary" in result

    # 测试股票查询
    result = agent.invoke({"user_query": "测试股票"})
    assert "summary" in result
