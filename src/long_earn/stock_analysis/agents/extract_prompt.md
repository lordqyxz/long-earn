---
version: 0.1.0
description: 股票名称提取提示词 - 从用户查询中提取股票名称和代码 - 自然语言处理中的实体提取 - query (str): 用户原始查询文本 - JSON 格式，包含： - stock_name: 提取的股票
tags:
  - prompt
---

# 提示词模板

股票名称提取提示词

适用场景：
- 从用户查询中提取股票名称和代码
- 自然语言处理中的实体提取

输入参数：
- query (str): 用户原始查询文本

输出格式：
- JSON 格式，包含：
  - stock_name: 提取的股票名称
  - stock_code: 提取的股票代码

使用示例：
    from long_earn.stock_analysis.agents.extract_prompt import extract_prompt

    formatted_prompt = extract_prompt.format(query=user_query)
    response = llm.invoke(formatted_prompt)
    result = json.loads(response.content)

注意事项：
- 如果无法提取，返回空字符串
- 需要处理各种自然语言表达方式

版本：0.1.0

## 提示词内容

请从以下用户查询中提取股票名称：

用户查询：${query}

请以 JSON 格式返回结果，包含以下字段：
- stock_name: 提取的股票名称（如果有）
- stock_code: 提取的股票代码（如果有）
如果无法提取，则返回空字符串。


## 输入变量

query
