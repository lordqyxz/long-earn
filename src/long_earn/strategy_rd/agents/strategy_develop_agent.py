from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from long_earn.core.prompt_loader import MarkdownPromptTemplate
from long_earn.services import KnowledgeService, LLMService, LoggerService

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext


class StrategyDevelopAgent:
    """策略开发智能体

    参考 LangGraph Runtime 实践：
    1. 依赖通过 context 传递
    2. 支持测试时注入 Mock
    """

    def __init__(self, context: "RuntimeContext"):
        """初始化策略开发 Agent

        Args:
            context: 运行时上下文
        """
        self.context = context
        self.llm_service = context.llm_service
        self.knowledge_service = context.knowledge_service
        self.logger = context.logger
        self._error_history: List[dict] = []

    def _search_knowledge(
        self,
        query: str,
        source_files: Optional[List[str]] = None,
    ) -> List[str]:
        """搜索知识库获取相关参考信息

        Args:
            query: 搜索查询
            source_files: 可选，按源文件过滤
        """
        try:
            results = self.knowledge_service.search(
                query, k=5, source_files=source_files
            )
            return results
        except Exception as e:
            if self.logger:
                self.logger.warning(f"搜索知识库失败：{e}")
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
        if not hasattr(self, "_develop_prompt"):
            self._develop_prompt = MarkdownPromptTemplate(
                "strategy_develop_prompt.md",
                ["strategy", "target_market", "backtest_params"],
                __file__,
            )

        strategy_info = strategy.get("description", str(strategy))
        strategy_name = strategy.get("strategy_name", "CustomStrategy")

        # 获取知识库上下文
        knowledge_context = self._get_knowledge_context(
            strategy_info, node_type="develop"
        )
        if knowledge_context:
            knowledge_context = "\n\n## 参考知识库:\n" + knowledge_context

        # 生成提示词并调用
        prompt = self._develop_prompt.format(
            strategy=strategy_info,
            target_market="A 股",
            backtest_params="默认参数",
        )
        if knowledge_context:
            prompt += knowledge_context

        # 解析 JSON 并提取代码
        response = self.llm_service.invoke(prompt).content
        code = self._extract_code_from_response(response)

        if self.logger:
            self.logger.info(f"策略开发完成：{strategy_name}")
        return code

    def _extract_code_from_response(self, response: str) -> str:
        """从响应中提取代码"""
        import json

        # 尝试解析 JSON
        try:
            # 查找 JSON 块
            if "```json" in response:
                json_str = response.split("```json")[1].split("```")[0].strip()
                data = json.loads(json_str)
                code = data.get("code", "")
            else:
                # 直接尝试解析整个响应
                data = json.loads(response)
                code = data.get("code", "")

            # code 字段已经是纯文本，直接返回
            return code.strip()
        except (json.JSONDecodeError, KeyError):
            # JSON 解析失败，直接提取代码块作为降级方案
            for start, end in [("```python", "```"), ("```", "```")]:
                if start in response:
                    return response.split(start)[1].split(end)[0].strip()
            return response

    def refine_code(
        self,
        strategy: Dict[str, Any],
        error_message: str,
        failed_code: str,
    ) -> str:
        """根据错误信息修复代码"""
        if not hasattr(self, "_refine_prompt"):
            self._refine_prompt = MarkdownPromptTemplate(
                "strategy_develop_refine_prompt.md",
                ["code", "strategy_description", "error_message"],
                __file__,
            )

        strategy_info = strategy.get("description", str(strategy))
        strategy_name = strategy.get("strategy_name", "CustomStrategy")

        # 记录错误
        self._error_history.append({"code": failed_code, "error": error_message})

        # 获取额外上下文
        experience_context = self._get_experience_context(strategy_info)
        knowledge_context = self._get_knowledge_context(
            error_message, node_type="develop"
        )

        # 构建提示词
        prompt = self._refine_prompt.format(
            code=failed_code,
            strategy_description=strategy_info,
            error_message=error_message,
        )
        if experience_context:
            prompt += experience_context
        if knowledge_context:
            prompt += "\n\n## 参考知识库:\n" + knowledge_context

        # 解析 JSON 并提取代码
        response = self.llm_service.invoke(prompt).content
        code = self._extract_code_from_response(response)

        if self.logger:
            self.logger.info(
                f"代码修复完成：{strategy_name} (尝试{len(self._error_history)}次)"
            )
        return code

    def _get_experience_context(self, strategy_info: str) -> str:
        """获取成功案例参考"""
        results = self._search_experience(strategy_info, min_sharpe=0.5)
        if not results:
            return ""

        context = "\n\n## 成功案例:\n"
        for exp in results[:2]:
            context += f"\n### {exp['name']}\n设计思路：{exp['rationale'][:200]}...\n```python\n{exp['code'][:500]}\n```\n"
        return context

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
            if self.logger:
                self.logger.warning(f"搜索经验失败：{e}")
            return []

    def get_error_history(self) -> List[dict]:
        """获取错误历史"""
        return self._error_history.copy()

    def clear_error_history(self):
        """清空错误历史"""
        self._error_history = []
