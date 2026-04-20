"""股票名称提取提示词

从用户查询中提取股票名称和代码的 PromptTemplate。

版本：0.1.0
"""

from langchain_core.prompts import PromptTemplate

__version__ = "0.1.0"

extract_prompt = PromptTemplate(
    input_variables=["query"],
    template="""请从以下用户查询中提取股票名称：

用户查询：{query}

请以 JSON 格式返回结果，包含以下字段：
- stock_name: 提取的股票名称（如果有）
- stock_code: 提取的股票代码（如果有）
如果无法提取，则返回空字符串。""",
)