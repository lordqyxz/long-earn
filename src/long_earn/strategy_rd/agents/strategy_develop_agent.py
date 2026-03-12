from pathlib import Path
from typing import Any, Dict

from long_earn.utils.logger import LOGGER


class StrategyDevelopAgent:
    """策略开发智能体"""

    def __init__(
        self, llm_type: str = "ollama", model_name: str = "qwen3.5:9b", base_url: str = ""
    ):
        self.llm_type = llm_type
        self.model_name = model_name
        self.base_url = base_url
        self._fallback_strategy_code = None

    def _create_llm(self):
        from long_earn.utils.llm_factory import create_llm

        return create_llm(
            llm_type=self.llm_type, model_name=self.model_name, base_url=self.base_url
        )

    def _load_fallback_strategy(self) -> str:
        """加载后备策略代码"""
        if self._fallback_strategy_code is not None:
            return self._fallback_strategy_code
        
        # 从文件读取后备策略
        fallback_file = Path(__file__).parent / "fallback_strategy.py"
        try:
            with open(fallback_file, 'r', encoding='utf-8') as f:
                self._fallback_strategy_code = f.read()
            return self._fallback_strategy_code
        except Exception as e:
            LOGGER.error(f"读取后备策略文件失败：{e}")
            # 如果文件读取失败，返回空字符串
            return ""

    def develop_strategy(self, strategy: Dict[str, Any]) -> str:
        """将策略转化为 pyqlib 回测格式"""
        from langchain_core.prompts import ChatPromptTemplate

        from .strategy_develop_prompt import strategy_develop_prompt

        llm = self._create_llm()

        try:
            strategy_info = strategy.get("description", str(strategy))
            strategy_name = strategy.get("strategy_name", "CustomStrategy")
            
            prompt = strategy_develop_prompt.format(
                strategy=strategy_info,
                target_market="A 股",
                backtest_params="默认参数"
            )
            
            response = llm.invoke(prompt)
            code = response.content
            
            # 提取代码块
            if "```python" in code:
                code = code.split("```python")[1].split("```")[0].strip()
            elif "```" in code:
                code = code.split("```")[1].split("```")[0].strip()
            
            LOGGER.info(f"策略开发完成：{strategy_name}")
            return code
            
        except Exception as e:
            LOGGER.error(f"策略开发失败：{e}")
            # 返回一个简单但有效的策略作为后备
            return self._load_fallback_strategy()
