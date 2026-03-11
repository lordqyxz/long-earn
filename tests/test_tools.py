import pytest
from long_earn.tools.subgraph_tool import SubgraphTool
from long_earn.tools.kimi_web_search import KimiWebSearch
from long_earn.tools.akshare import get_stock_data, get_index_data
from long_earn.tools.code_safety_check import CodeSafetyCheck


def test_kimi_web_search():
    """测试kimi web search工具"""
    search_tool = KimiWebSearch()
    result = search_tool.search("测试搜索")
    assert len(result) == 2
    assert "title" in result[0]
    assert "url" in result[0]
    assert "content" in result[0]


def test_akshare():
    """测试akshare工具"""
    stock_data = get_stock_data("600519")
    assert stock_data["code"] == "600519"
    assert "name" in stock_data
    assert "current_price" in stock_data
    
    index_data = get_index_data("000001")
    assert index_data["code"] == "000001"
    assert "name" in index_data
    assert "price" in index_data


def test_code_safety_check():
    """测试代码安全检查工具"""
    safety_check = CodeSafetyCheck()
    safe_code = "print('hello world')"
    result = safety_check.check(safe_code)
    assert result["safe"] == True
    
    unsafe_code = "eval('print(123)')"
    result = safety_check.check(unsafe_code)
    assert result["safe"] == False
    assert len(result["issues"]) > 0
