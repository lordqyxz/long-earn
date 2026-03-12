import os

from typing import Any, Dict, List

from openai import OpenAI


def kimi_web_search(query: str) -> List[Dict[str, Any]]:
    """使用Kimi API的$web_search内置函数进行联网搜索

    Args:
        query: 搜索关键词

    Returns:
        搜索结果列表，每个元素包含title, url, content
    """
    api_key = os.environ.get("MOONSHOT_API_KEY")
    if not api_key:
        api_key = os.environ.get("KIMI_API_KEY")

    if not api_key:
        raise ValueError("请设置环境变量 MOONSHOT_API_KEY 或 KIMI_API_KEY")

    client = OpenAI(api_key=api_key, base_url="https://api.moonshot.cn/v1")

    tools = [{"type": "builtin_function", "function": {"name": "$web_search"}}]

    response = client.chat.completions.create(
        model="kimi-k2-turbo-preview",
        messages=[{"role": "user", "content": query}],
        tools=tools,
    )

    if response.choices[0].finish_reason == "tool_calls":
        tool_calls = response.choices[0].message.tool_calls
        arguments = tool_calls[0].function.arguments

        tool_response = client.chat.completions.create(
            model="kimi-k2-turbo-preview",
            messages=[
                {"role": "user", "content": query},
                {"role": "assistant", "tool_calls": tool_calls},
                {
                    "role": "tool",
                    "tool_call_id": tool_calls[0].id,
                    "content": arguments,
                },
            ],
            tools=tools,
        )

        content = tool_response.choices[0].message.content

        results = []
        if isinstance(content, str):
            results.append({"title": "搜索结果", "url": "", "content": content})
        elif isinstance(content, list):
            for item in content:
                if item.get("type") == "text":
                    results.append(
                        {
                            "title": "搜索结果",
                            "url": "",
                            "content": item.get("text", ""),
                        }
                    )

        return results

    return []


if __name__ == "__main__":
    query = "测试搜索"
    results = kimi_web_search(query)
    for result in results:
        print(result)
