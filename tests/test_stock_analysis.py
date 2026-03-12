from unittest.mock import Mock, patch

import pytest

from long_earn.stock_analysis.subgraph import create_stock_analysis_subgraph


def test_stock_analysis_subgraph():
    """测试股票分析子图"""
    # 创建子图
    subgraph = create_stock_analysis_subgraph()

    # 测试子图执行
    result = subgraph.invoke({"query": "测试股票分析"})

    # 验证结果
    assert "result" in result
    assert "股票分析汇总" in result["result"]


def test_stock_analysis_with_stock_code():
    """测试直接提供股票代码的情况"""
    # 创建子图
    subgraph = create_stock_analysis_subgraph()

    # 测试子图执行，直接提供股票代码
    result = subgraph.invoke({"stock_code": "600519"})  # 贵州茅台

    # 验证结果
    assert "result" in result
    assert "股票分析汇总" in result["result"]


def test_stock_analysis_with_stock_name():
    """测试通过查询提取股票名称的情况"""
    # 创建子图
    subgraph = create_stock_analysis_subgraph()

    # 测试子图执行，提供包含股票名称的查询
    result = subgraph.invoke({"query": "帮我分析贵州茅台这个股票"})

    # 验证结果
    assert "result" in result
    assert "股票分析汇总" in result["result"]


@patch("long_earn.utils.llm_factory.create_llm")
def test_stock_analysis_llm_extraction(mock_create_llm):
    """测试大模型提取股票名称的功能"""
    # 创建mock LLM
    mock_llm = Mock()
    mock_response = Mock()
    mock_response.content = '{"stock_name": "贵州茅台", "stock_code": ""}'
    mock_llm.invoke.return_value = mock_response
    mock_create_llm.return_value = mock_llm

    # 创建子图
    subgraph = create_stock_analysis_subgraph()

    # 测试子图执行
    result = subgraph.invoke({"query": "帮我分析贵州茅台这个股票"})

    # 验证结果
    assert "result" in result
    assert "股票分析汇总" in result["result"]


@patch("long_earn.utils.llm_factory.create_llm")
def test_stock_analysis_llm_failure(mock_create_llm):
    """测试大模型调用失败时的回退机制"""
    # 创建mock LLM，模拟调用失败
    mock_llm = Mock()
    mock_llm.invoke.side_effect = Exception("LLM Error")
    mock_create_llm.return_value = mock_llm

    # 创建子图
    subgraph = create_stock_analysis_subgraph()

    # 测试子图执行
    result = subgraph.invoke({"query": "帮我分析贵州茅台这个股票"})

    # 验证结果
    assert "result" in result
    assert "股票分析汇总" in result["result"]
