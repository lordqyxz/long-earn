from typing import Dict, Any
from langchain_core.language_models import BaseLanguageModel
from long_earn.stock_analysis.agents.charles_munger_prompt import charles_munger_prompt
import os
from long_earn.utils.llm_factory import create_llm


class CharlesMungerAnalyst:
    """查理芒格视角的股票分析智能体"""
    def __init__(self, llm=None):
        self.llm = llm or create_llm(
            llm_type=os.getenv("LLM_TYPE", "ollama"),
            model_name=os.getenv("LLM_MODEL", "qwen3.5:cloud")
        )
        self.prompt = charles_munger_prompt
    
    def analyze(self, stock_data: Dict[str, Any]) -> str:
        """分析股票"""
        # 格式化提示词
        formatted_prompt = self.prompt.format(stock_data=stock_data)
        
        # 调用LLM生成分析
        response = self.llm.invoke(formatted_prompt)
        
        return response.content if hasattr(response, 'content') else str(response)