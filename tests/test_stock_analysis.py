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
