import os

from tavily import TavilyClient

tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))


def tavily_search(query: str) -> dict:
    """使用tavily search工具搜索互联网

    Args:
        query: 搜索查询字符串

    Returns:
        包含搜索结果的字典
    """
    response = tavily_client.search(query)
    return response


if __name__ == "__main__":
    query = "测试搜索"
    results = tavily_search(query)
    for result in results["results"]:
        print(result["title"])
        print(result["url"])
        print(result["content"])
        print("\n")
