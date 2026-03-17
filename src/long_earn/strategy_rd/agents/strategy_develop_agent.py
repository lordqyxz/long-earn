from pathlib import Path
from typing import Any, Dict, List, Optional

from long_earn.utils.logger import LOGGER


class StrategyDevelopAgent:
    """策略开发智能体"""

    def __init__(
        self,
        llm_type: str = "ollama",
        model_name: str = "qwen3.5:9b",
        base_url: str = "",
    ):
        self.llm_type = llm_type
        self.model_name = model_name
        self.base_url = base_url
        self._error_history: List[dict] = []

    def _create_llm(self):
        from long_earn.utils.llm_factory import create_llm

        return create_llm(
            llm_type=self.llm_type or "ollama",
            model_name=self.model_name or "qwen3.5:cloud",
            base_url=self.base_url or "http://localhost:11434",
        )

    def _search_knowledge(
        self,
        query: str,
        source_files: Optional[List[str]] = None,
    ) -> List[str]:
        """搜索知识库获取相关参考信息

        Args:
            query: 搜索查询
            source_files: 可选，按源文件过滤 (如 ["01_data.md", "02_strategy.md"])
        """
        try:
            from long_earn.tools.store import search_knowledge

            results = search_knowledge(query, k=5, source_files=source_files)
            return results
        except Exception as e:
            LOGGER.warning(f"搜索知识库失败: {e}")
            return []

    def _get_knowledge_context(self, query: str, node_type: str = "develop") -> str:
        """获取知识库上下文

        Args:
            query: 搜索查询
            node_type: 节点类型，"develop" 时检索代码相关知识
        """
        if node_type == "develop":
            source_files = [
                "01_data.md",
                "02_strategy.md",
                "03_signals.md",
                "04_backtest.md",
                "05_metrics.md",
                "06_errors.md",
                "07_example.md",
            ]
            results = self._search_knowledge(query, source_files=source_files)
        else:
            results = self._search_knowledge(query)

        if results:
            return "\n".join(results)
        return ""

    def develop_strategy(self, strategy: Dict[str, Any]) -> str:
        """将策略转化为 pyqlib 回测格式"""
        from langchain_core.prompts import ChatPromptTemplate

        from .strategy_develop_prompt import strategy_develop_prompt

        llm = self._create_llm()

        strategy_info = strategy.get("description", str(strategy))
        strategy_name = strategy.get("strategy_name", "CustomStrategy")

        knowledge_context = self._get_knowledge_context(
            strategy_info, node_type="develop"
        )
        if knowledge_context:
            knowledge_context = "\n\n## 参考知识库内容:\n" + knowledge_context
            LOGGER.info("成功获取知识库参考信息")

        prompt = strategy_develop_prompt.format(
            strategy=strategy_info,
            target_market="A 股",
            backtest_params="默认参数",
        )

        if knowledge_context:
            prompt = prompt + knowledge_context

        response = llm.invoke(prompt)
        code = response.content

        if "```python" in code:
            code = code.split("```python")[1].split("```")[0].strip()
        elif "```" in code:
            code = code.split("```")[1].split("```")[0].strip()

        LOGGER.info(f"策略开发完成：{strategy_name}")
        return code

    def refine_code(
        self,
        strategy: Dict[str, Any],
        error_message: str,
        failed_code: str,
    ) -> str:
        """根据错误信息修复代码

        Args:
            strategy: 策略信息
            error_message: 错误信息
            failed_code: 失败的代码

        Returns:
            修复后的代码
        """
        from langchain_core.prompts import ChatPromptTemplate

        from .strategy_develop_prompt import strategy_develop_refine_prompt

        llm = self._create_llm()

        strategy_info = strategy.get("description", str(strategy))
        strategy_name = strategy.get("strategy_name", "CustomStrategy")

        self._error_history.append(
            {
                "code": failed_code,
                "error": error_message,
            }
        )

        experience_results = self._search_experience(strategy_info, min_sharpe=0.5)
        experience_context = ""
        if experience_results:
            experience_context = "\n\n## 成功案例参考:\n"
            for exp in experience_results[:2]:
                experience_context += f"""
### {exp['name']}
设计思路: {exp['rationale'][:200]}...
代码:
```python
{exp['code'][:500]}
```
"""

        knowledge_context = self._get_knowledge_context(
            error_message, node_type="develop"
        )
        if knowledge_context:
            knowledge_context = "\n\n## 参考知识库:\n" + knowledge_context

        prompt = strategy_develop_refine_prompt.format(
            code=failed_code,
            strategy_description=strategy_info,
            error_message=error_message,
        )

        if experience_context:
            prompt = prompt + experience_context
        if knowledge_context:
            prompt = prompt + knowledge_context

        response = llm.invoke(prompt)
        code = response.content

        if "```python" in code:
            code = code.split("```python")[1].split("```")[0].strip()
        elif "```" in code:
            code = code.split("```")[1].split("```")[0].strip()

        LOGGER.info(
            f"代码修复完成：{strategy_name} (错误尝试 {len(self._error_history)} 次)"
        )
        return code

    def _search_experience(
        self,
        query: str,
        min_sharpe: Optional[float] = None,
    ) -> List[dict]:
        """搜索历史策略经验

        Args:
            query: 搜索查询
            min_sharpe: 最小夏普比率

        Returns:
            经验列表
        """
        try:
            from long_earn.tools.store import search_experience

            return search_experience(query, k=2, min_sharpe=min_sharpe)
        except Exception as e:
            LOGGER.warning(f"搜索经验失败: {e}")
            return []

    def get_error_history(self) -> List[dict]:
        """获取错误历史"""
        return self._error_history.copy()

    def clear_error_history(self):
        """清空错误历史"""
        self._error_history = []
