import json

from typing import Any, Dict

from long_earn.utils.logger import LOGGER


class StrategyResearchAgent:
    """策略研究智能体"""

    def __init__(
        self, llm_type: str = "dashscope", model_name: str = "qwen3-max-2026-01-23", base_url: str = ""
    ):
        self.llm_type = llm_type
        self.model_name = model_name
        self.base_url = base_url

    def _create_llm(self):
        from long_earn.utils.llm_factory import create_llm

        return create_llm(
            llm_type=self.llm_type, model_name=self.model_name, base_url=self.base_url
        )

    def research_strategy(self, query: str) -> Dict[str, Any]:
        """研究策略 - 根据用户查询生成初始策略"""
        from langchain_core.prompts import ChatPromptTemplate

        from .strategy_research_prompt import create_strategy_research_prompt

        llm = self._create_llm()

        try:
            prompt = create_strategy_research_prompt(
                target_market="stock",
                query=query,
                strategy_examples="无",
                strategy_context="无",
            )
            # 将 ChatPromptTemplate 转换为消息列表
            if isinstance(prompt, ChatPromptTemplate):
                messages = prompt.format_messages()
            else:
                messages = prompt
            response = llm.invoke(messages)
            LOGGER.info(f"策略研究代理生成策略完成：{query}")

            return {
                "strategy_name": "研究策略",
                "description": response.content,
                "query": query,
            }
        except Exception as e:
            LOGGER.error(f"策略研究失败: {e}")
            return {
                "strategy_name": "默认策略",
                "description": "趋势跟踪策略，基于移动平均线和成交量因子",
                "query": query,
            }

    def reflect(
        self, strategy: Dict[str, Any], backtest_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """反思 - 分析回测结果并生成改进建议"""
        from .strategy_research_prompt import strategy_reflection_prompt

        llm = self._create_llm()

        try:
            prompt = strategy_reflection_prompt.format(
                strategy=strategy,
                backtest_result=backtest_result,
                reflection_history="无",
            )
            response = llm.invoke(prompt)

            content = response.content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            result = json.loads(content)
            LOGGER.info("策略反思完成")

            return {
                "reflection": result.get("reflection", ""),
                "improvement_suggestions": result.get("improvement_suggestions", []),
            }
        except Exception as e:
            LOGGER.error(f"策略反思失败: {e}")
            return {
                "reflection": "策略表现一般，需要进一步优化",
                "improvement_suggestions": ["调整止损策略", "优化参数"],
            }

    def optimize_strategy(
        self, strategy: Dict[str, Any], improvement_suggestions: list
    ) -> Dict[str, Any]:
        """优化策略 - 根据改进建议优化策略"""
        from .strategy_research_prompt import strategy_optimize_prompt

        llm = self._create_llm()

        try:
            suggestions_str = "\n".join([f"- {s}" for s in improvement_suggestions])
            prompt = strategy_optimize_prompt.format(
                strategy=strategy,
                suggestions_text=suggestions_str,
                backtest_history="无",
                market_characteristics="无",
            )
            response = llm.invoke(prompt)

            optimized = strategy.copy()
            optimized["description"] = response.content
            optimized["optimized"] = True
            LOGGER.info("策略优化完成")

            return optimized
        except Exception as e:
            LOGGER.error(f"策略优化失败: {e}")
            strategy["optimized"] = True
            return strategy
