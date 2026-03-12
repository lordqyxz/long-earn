import os

from typing import Any, Dict, Optional

from langchain_core.language_models import BaseLanguageModel

from long_earn.utils.llm_factory import create_llm


class BuffettAnalyst:
    """巴菲特视角的股票分析智能体"""

    def __init__(self, llm: Optional[BaseLanguageModel] = None):
        self.llm = llm or create_llm(
            llm_type=os.getenv("LLM_TYPE", "ollama"),
            model_name=os.getenv("LLM_MODEL", "qwen3.5:cloud"),
        )
        # 动态导入prompt以避免循环导入
        from .buffett_prompt import buffett_prompt_template

        self.prompt = buffett_prompt_template

    def analyze(self, stock_data: Dict[str, Any]) -> str:
        """分析股票"""
        # 格式化提示词
        formatted_prompt = self.prompt.format(stock_data=stock_data)

        # 调用LLM生成分析
        response = self.llm.invoke(formatted_prompt)

        return response.content if hasattr(response, "content") else str(response)
