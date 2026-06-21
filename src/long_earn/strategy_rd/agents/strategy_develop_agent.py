import json
import re
from typing import TYPE_CHECKING, Any

from long_earn.core.llm_utils import parse_llm_json
from long_earn.core.prompt_loader import MarkdownPromptTemplate
from long_earn.strategy_rd.agents.mixins import KnowledgeContextMixin

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext

_MAX_EXPERIENCES = 2

_DEVELOP_SOURCE_FILES = [
    "01_data.md",
    "02_strategy.md",
    "03_signals.md",
    "04_backtest.md",
    "05_metrics.md",
    "06_errors.md",
    "07_example.md",
]


class StrategyDevelopAgent(KnowledgeContextMixin):
    """策略开发智能体 — 将策略转化为 YAML DSL

    通过 KnowledgeContextMixin 复用统一的知识检索和缓存逻辑。
    """

    def __init__(self, context: "RuntimeContext"):
        self.context = context
        self.llm_service = context.require_llm()
        self.memory = context.require_memory()
        self.logger = context.logger
        self._knowledge_cache: dict[str, list[str]] = {}
        self._error_history: list[dict] = []

    def _get_develop_context(self, query: str) -> str:
        """获取开发相关参考知识（指定源文件范围）"""
        return self._get_knowledge_context(
            query, node_type="develop", source_files=_DEVELOP_SOURCE_FILES
        )

    def develop_strategy(self, strategy: dict[str, Any]) -> str:
        """将策略转化为 YAML 回测格式"""
        if not hasattr(self, "_develop_prompt"):
            self._develop_prompt = MarkdownPromptTemplate(
                "strategy_develop_prompt.md",
                ["strategy", "target_market", "backtest_params"],
                __file__,
            )

        strategy_info = strategy.get("description", str(strategy))
        strategy_name = strategy.get("strategy_name", "CustomStrategy")

        knowledge_context = self._get_develop_context(strategy_info)
        if knowledge_context:
            knowledge_context = "\n\n## 参考知识库:\n" + knowledge_context

        prompt = self._develop_prompt.format(
            strategy=strategy_info,
            target_market="A 股",
            backtest_params="默认参数",
        )
        if knowledge_context:
            prompt += knowledge_context

        response = self.llm_service.invoke(prompt).content
        yaml_str = self._extract_yaml_from_response(response)

        if self.logger:
            self.logger.info(f"策略开发完成：{strategy_name}")
        return yaml_str

    def _extract_yaml_from_response(self, response: str) -> str:
        """从响应中提取 YAML 策略"""

        try:
            data = parse_llm_json(response)
            yaml_str = data.get("strategy_yaml", "")
            if yaml_str:
                return yaml_str.strip()
        except (json.JSONDecodeError, KeyError):
            pass

        for start, end in [("```yaml", "```"), ("```", "```")]:
            if start in response:
                yaml_str = response.split(start)[1].split(end, maxsplit=1)[0].strip()
                return self._clean_yaml_trailing_content(yaml_str)

        # 无代码块标记时，尝试从 strategy: 开始截取
        cleaned = self._clean_yaml_trailing_content(response.strip())
        return cleaned

    @staticmethod
    def _clean_yaml_trailing_content(yaml_str: str) -> str:
        """清理 YAML 末尾附带的非 YAML 内容（如 JSON 解释文本）

        LLM 有时在 YAML 代码块后附加 JSON 格式的说明，
        导致 YAML 解析器在遇到 '{' 时报错。
        """
        lines = yaml_str.split("\n")
        clean_lines: list[str] = []
        for line in lines:
            # 遇到非缩进的 { 开头行，视为 YAML 之后的附加内容，截断
            if line.startswith("{") and not line.startswith("  "):
                break
            clean_lines.append(line)
        return "\n".join(clean_lines).strip()

    def refine_code(
        self,
        strategy: dict[str, Any],
        error_message: str,
        failed_code: str,
    ) -> str:
        """根据错误信息修复策略 YAML"""
        if not hasattr(self, "_refine_prompt"):
            self._refine_prompt = MarkdownPromptTemplate(
                "strategy_develop_refine_prompt.md",
                ["code", "strategy_description", "error_message"],
                __file__,
            )

        strategy_info = strategy.get("description", str(strategy))
        strategy_name = strategy.get("strategy_name", "CustomStrategy")

        self._error_history.append({"code": failed_code, "error": error_message})

        experience_context = self._get_experience_context(strategy_info)
        knowledge_context = self._get_develop_context(error_message)

        prompt = self._refine_prompt.format(
            code=failed_code,
            strategy_description=strategy_info,
            error_message=error_message,
        )
        if experience_context:
            prompt += experience_context
        if knowledge_context:
            prompt += "\n\n## 参考知识库:\n" + knowledge_context

        response = self.llm_service.invoke(prompt).content
        yaml_str = self._extract_yaml_from_response(response)

        if self.logger:
            self.logger.info(
                f"策略修复完成：{strategy_name} (尝试{len(self._error_history)}次)"
            )
        return yaml_str

    def _get_experience_context(self, strategy_info: str) -> str:
        """获取成功案例参考"""
        results = self._search_experience(strategy_info, min_sharpe=0.5)
        if not results:
            return ""

        context = "\n\n## 成功案例:\n"
        for exp in results[:2]:
            context += (
                f"\n### {exp['name']}\n"
                f"设计思路：{exp['rationale'][:200]}...\n"
                f"```python\n{exp['code'][:500]}\n```\n"
            )
        return context

    def _search_experience(
        self,
        query: str,
        min_sharpe: float | None = None,
    ) -> list[dict]:
        """搜索历史策略经验"""

        try:
            raw = self.memory.recall(
                query, k=4, categories=["策略经验"]
            )
        except Exception:
            if self.logger:
                self.logger.warning(f"搜索经验失败：{query}")
            return []

        results: list[dict] = []
        for r in raw:
            meta = r["metadata"]
            if min_sharpe:
                sharpe = meta.get("sharpe_ratio", 0)
                if sharpe < min_sharpe:
                    continue

            content = r["content"]
            code_match = re.search(r"```python\n(.*?)```", content, re.DOTALL)
            rationale_match = re.search(
                r"## 设计思路\n(.*?)## 策略代码", content, re.DOTALL
            )

            results.append(
                {
                    "name": meta.get("term", ""),
                    "code": code_match.group(1).strip() if code_match else "",
                    "rationale": (
                        rationale_match.group(1).strip() if rationale_match else ""
                    ),
                    "metrics": meta,
                }
            )

            if len(results) >= _MAX_EXPERIENCES:
                break

        return results

    def get_error_history(self) -> list[dict]:
        """获取错误历史"""
        return self._error_history.copy()

    def clear_error_history(self) -> None:
        """清空错误历史"""
        self._error_history = []
