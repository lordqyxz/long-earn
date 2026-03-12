from long_earn.tools.kimi_web_search import kimi_web_search


def test_kimi_web_search():
    """测试kimi web search工具"""
    result = kimi_web_search("测试搜索")
    assert len(result) == 2
    assert "title" in result[0]
    assert "url" in result[0]
    assert "content" in result[0]


if __name__ == "__main__":
    test_kimi_web_search()
