"""LLM 响应解析工具

处理 LLM 返回的 JSON 响应，自动剥离 markdown 代码块包裹。
"""

import json
import re


def parse_llm_json(text: str) -> dict:
    """从 LLM 响应中解析 JSON

    自动处理以下常见格式：
    - 纯 JSON 字符串
    - markdown 代码块包裹：```json ... ``` 或 ``` ... ```
    - 前后有多余空白或换行

    Args:
        text: LLM 返回的原始文本

    Returns:
        解析后的字典

    Raises:
        json.JSONDecodeError: 无法解析为有效 JSON
    """
    content = text.strip()

    # 尝试剥离 ```json ... ``` 包裹
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", content, re.DOTALL)
    if match:
        content = match.group(1).strip()

    # 尝试直接解析
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 尝试提取第一个 { ... } 块
    brace_match = re.search(r"\{.*\}", content, re.DOTALL)
    if brace_match:
        return json.loads(brace_match.group(0))

    # 全部失败，抛出原始错误
    raise json.JSONDecodeError("Expecting value", text, 0)


def sanitize_code(code: str) -> str:
    """清洗代码中的全角字符

    将中文全角标点替换为 ASCII 半角标点，避免 Python 语法错误。

    Args:
        code: 原始代码

    Returns:
        清洗后的代码
    """
    # 全角 → 半角映射
    fullwidth_map = {
        "\u3001": ",",  # 、→ ,
        "\u3002": ".",  # 。→ .
        "\uff0c": ",",  # ，→ ,
        "\uff0e": ".",  # ．→ .
        "\uff1a": ":",  # ：→ :
        "\uff1b": ";",  # ；→ ;
        "\uff08": "(",  # （→ (
        "\uff09": ")",  # ）→ )
        "\u300a": "<",  # 《→ <
        "\u300b": ">",  # 》→ >
        "\u3010": "[",  # 【→ [
        "\u3011": "]",  # 】→ ]
        "\u2018": "'",  # '→ '
        "\u2019": "'",  # '→ '
        "\u201c": '"',  # "→ "
        "\u201d": '"',  # "→ "
        "\uff5b": "{",  # ｛→ {
        "\uff5d": "}",  # ｝→ }
    }

    for fullwidth, halfwidth in fullwidth_map.items():
        code = code.replace(fullwidth, halfwidth)

    return code