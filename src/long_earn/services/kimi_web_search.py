import os
from typing import Any, cast

from openai import OpenAI

# moonshot 的 $web_search 是厂商扩展内置工具，OpenAI SDK 的
# ChatCompletionToolParam 联合类型不含该类型，用 Any 兜底
# （第三方库扩展类型不兼容，见 AGENTS.md Any 兜底规则）
_WEB_SEARCH_TOOLS: Any = [
    {"type": "builtin_function", "function": {"name": "$web_search"}}
]

# OpenAI SDK 默认超时 600s × 自动重试，同步阻塞调用方（collect 节点）可挂数
# 十分钟；显式收紧以保证请求失败可中断、可快速暴露。
KIMI_REQUEST_TIMEOUT_SECONDS = 60.0
KIMI_MAX_RETRIES = 2


def kimi_web_search(query: str) -> list[dict[str, Any]]:
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

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.moonshot.cn/v1",
        timeout=KIMI_REQUEST_TIMEOUT_SECONDS,
        max_retries=KIMI_MAX_RETRIES,
    )

    response = client.chat.completions.create(
        model="kimi-k2-turbo-preview",
        messages=[{"role": "user", "content": query}],
        tools=_WEB_SEARCH_TOOLS,
    )

    if response.choices[0].finish_reason == "tool_calls":
        tool_calls = response.choices[0].message.tool_calls
        if not tool_calls:
            return []
        first_call: Any = tool_calls[0]
        arguments = first_call.function.arguments

        # assistant 段回带 tool_calls、tool 段回填调用结果；消息列表由
        # OpenAI SDK 联合类型推导会有噪音，显式标注 list[Any] 规避
        tool_messages: list[Any] = [
            {"role": "user", "content": query},
            {"role": "assistant", "tool_calls": tool_calls},
            {"role": "tool", "tool_call_id": first_call.id, "content": arguments},
        ]
        tool_response = client.chat.completions.create(
            model="kimi-k2-turbo-preview",
            messages=tool_messages,
            tools=_WEB_SEARCH_TOOLS,
        )

        content = tool_response.choices[0].message.content

        results: list[dict[str, Any]] = []
        if isinstance(content, str):
            results.append({"title": "搜索结果", "url": "", "content": content})
        elif isinstance(content, list):
            # OpenAI SDK 的 content 联合类型在 stub 中推导噪音大（list 分支
            # 收敛为 Never，带注解赋值仍会被收窄），显式 cast 为 list[Any]
            # 以遍历文本分片
            parts = cast(list[Any], content)
            for item in parts:
                if isinstance(item, dict) and item.get("type") == "text":
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
