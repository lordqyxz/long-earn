import json
from typing import TYPE_CHECKING, Any

from langchain_core.prompts import ChatPromptTemplate, PromptTemplate

from long_earn.core.llm_utils import parse_llm_json
from long_earn.strategy_rd.agents.mixins import KnowledgeContextMixin

from .strategy_research_prompt import (
    create_strategy_research_prompt,
    strategy_optimize_prompt,
)

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext

_DRAWDOWN_RISK_THRESHOLD = 30
_DRAWDOWN_MODERATE_THRESHOLD = 20
_MIN_SHARPE_THRESHOLD = 0.5
_POOR_SHARPE_THRESHOLD = 0.3
_MIN_RETURN_THRESHOLD = 10

OPTIMIZATION_DIRECTIONS = {
    "收益增强": {
        "focus": "关注策略收益最大化",
        "metrics": ["return", "information_ratio"],
        "typical_improvements": [
            "新增因子",
            "调整因子权重",
            "扩展选股池",
            "优化入场时机",
        ],
        "categories": ["一、基础指标类", "二、技术分析类", "五、量化策略类"],
    },
    "风险控制": {
        "focus": "关注风险控制，减少回撤和波动",
        "metrics": ["max_drawdown", "volatility"],
        "typical_improvements": [
            "添加止损机制",
            "动态仓位调整",
            "对冲策略",
            "降低持仓集中度",
        ],
        "categories": ["四、风险指标类", "五、量化策略类"],
    },
    "收益稳定性": {
        "focus": "关注收益稳定性和风险调整收益",
        "metrics": ["sharpe_ratio", "calmar_ratio"],
        "typical_improvements": ["因子择时", "策略轮动", "风险平价配置", "自适应参数"],
        "categories": ["四、风险指标类", "五、量化策略类"],
    },
}


NODE_CATEGORIES = {
    "research": [
        "一、基础指标类",
        "二、技术分析类",
        "三、财务分析类",
        "五、量化策略类",
        "六、证券分析类",
    ],
    "reflection": ["四、风险指标类", "五、量化策略类"],
    "optimize": ["三、财务分析类", "四、风险指标类", "五、量化策略类"],
    "develop": None,
}


class StrategyResearchAgent(KnowledgeContextMixin):
    """策略研究智能体 — 研究策略并生成迭代改进

    通过 KnowledgeContextMixin 复用统一的知识检索和缓存逻辑。
    """

    def __init__(self, context: "RuntimeContext"):
        self.context = context
        self.llm_service = context.llm_service
        self.memory = context.memory
        self.logger = context.logger
        self._knowledge_cache: dict[str, list[str]] = {}

    def _get_research_context(self, query: str, node_type: str | None = None) -> str:
        """获取研究相关知识（按类别过滤）"""
        categories = NODE_CATEGORIES.get(node_type) if node_type else None
        return KnowledgeContextMixin._get_knowledge_context(
            self, query, node_type=node_type, categories=categories
        )

    def _create_retrieval_decision_prompt(self, query: str, context: str) -> str:
        return f"""<task>
判断是否需要从知识库检索更多信息来回答用户查询。
</task>

<user_query>
{query}
</user_query>

<current_context>
{context if context else "无"}
</current_context>

<instructions>
分析当前上下文是否足够回答用户查询。如果需要更多信息，返回需要检索的关键词/问题。
如果当前上下文已经足够，返回 "SUFFICIENT"。
</instructions>

<output_format>
请返回以下格式：
- 如果需要检索: "RETRIEVE: <检索关键词1>, <检索关键词2>"
- 如果已足够: "SUFFICIENT"
"""

    def _should_retrieve(
        self, query: str, current_context: str
    ) -> tuple[bool, list[str]]:
        """判断是否需要检索，返回 (是否需要，检索关键词列表)"""
        if self.logger:
            self.logger.info("[检索评估] 调用 LLM 判断是否需要更多检索...")

        response = self.llm_service.invoke(
            self._create_retrieval_decision_prompt(query, current_context)
        )

        content = response.content.strip()
        if self.logger:
            self.logger.info(f"[检索评估] LLM 响应: {content[:80]}")
        if content.startswith("SUFFICIENT"):
            return False, []
        elif content.startswith("RETRIEVE:"):
            keywords = content[9:].split(",")
            return True, [k.strip() for k in keywords]
        return False, []

    def research_strategy(self, query: str) -> dict[str, Any]:
        """研究策略 - 根据用户查询生成初始策略"""

        knowledge_context = self._get_research_context(query, node_type="research")

        prompt = create_strategy_research_prompt(
            target_market="stock",
            query=query,
            strategy_examples="无",
            strategy_context=knowledge_context if knowledge_context else "无",
        )
        if isinstance(prompt, ChatPromptTemplate):
            messages = prompt.format_messages()
        else:
            messages = prompt
        response = self.llm_service.invoke(messages)
        if self.logger:
            self.logger.info(f"策略研究代理生成策略完成：{query}")

        return {
            "strategy_name": "研究策略",
            "description": response.content,
            "query": query,
        }

    def research_strategy_with_context(
        self, query: str, knowledge_context: str = ""
    ) -> dict[str, Any]:
        """使用已有上下文的研究策略"""

        if self.logger:
            self.logger.info(f"[策略研究Agent] 开始研究: {query}")

        prompt_template = create_strategy_research_prompt(
            target_market="stock",
            query=query,
            strategy_examples="无",
            strategy_context=knowledge_context if knowledge_context else "无",
        )

        if isinstance(prompt_template, ChatPromptTemplate):
            messages = prompt_template.format_messages()
        elif isinstance(prompt_template, PromptTemplate):
            messages = prompt_template.format(
                target_market="stock",
                query=query,
                strategy_examples="无",
                strategy_context=knowledge_context if knowledge_context else "无",
            )
        else:
            messages = prompt_template

        response = self.llm_service.invoke(messages)
        if self.logger:
            self.logger.info("策略研究代理生成策略完成（使用自适应检索上下文）")

        return {
            "strategy_name": "研究策略",
            "description": response.content,
            "query": query,
        }

    def _identify_primary_issue(self, backtest_result: dict[str, Any]) -> str:
        """根据回测指标自动判断主要问题方向"""
        metrics = backtest_result.get("metrics", {})
        if not metrics:
            return "收益增强"

        return_rate = metrics.get("return", 0) or metrics.get("annual_return", 0)
        max_drawdown = abs(metrics.get("max_drawdown", 0) or metrics.get("drawdown", 0))
        sharpe = metrics.get("sharpe_ratio", 0) or metrics.get("sharpe", 0)

        if max_drawdown > _DRAWDOWN_RISK_THRESHOLD:
            return "风险控制"
        elif return_rate < 0:
            return "收益增强"
        elif sharpe < _MIN_SHARPE_THRESHOLD:
            return "收益稳定性"
        else:
            return "收益增强"

    def _build_reflection_prompt(
        self, direction: str, strategy: dict[str, Any], backtest_result: dict[str, Any]
    ) -> str:
        """构建特定方向的反思提示"""
        direction_config = OPTIMIZATION_DIRECTIONS.get(
            direction, OPTIMIZATION_DIRECTIONS["收益增强"]
        )

        prompt = f"""<role>
你是一位资深的量化策略分析师，专注于{direction}方向。你的分析以数据为依据，逻辑严密。
</role>

<context>
当前策略：
<strategy>
{json.dumps(strategy, ensure_ascii=False, indent=2)}
</strategy>

回测结果：
<backtest_result>
{json.dumps(backtest_result, ensure_ascii=False, indent=2)}
</backtest_result>

<focus>
{direction_config["focus"]}
</focus>

<analysis_framework>
请从以下维度进行{direction}分析：

1. 当前策略在{direction}方面的表现
2. 存在的主要问题及原因
3. 可行的改进方案
4. 预期改进效果
</analysis_framework>

<thinking_process>
在给出建议前，请按步骤思考：
1. 首先识别回测结果中的关键指标
2. 分析当前策略在{direction}方面的问题根源
3. 提出具体可执行的改进方案
</thinking_process>

<output_format>
请严格按照以下JSON格式返回分析结果：
```json
{{
    "direction": "{direction}",
    "reflection": "详细的反思内容，包含问题诊断和原因分析",
    "improvement_suggestions": [
        {{
            "priority": "高/中/低",
            "issue": "发现的问题",
            "suggestion": "具体改进建议",
            "expected_impact": "预期改进效果"
        }}
    ]
}}
```
</output_format>"""

        return prompt

    def _run_branch_reflection(
        self, direction: str, strategy: dict[str, Any], backtest_result: dict[str, Any]
    ) -> dict[str, Any]:
        """运行单个方向的反思"""
        if self.logger:
            self.logger.info(f"[ToT反思] 开始分支: {direction}")

        knowledge_context = self._get_research_context(
            f"策略{direction}方法", node_type="reflection"
        )
        prompt = self._build_reflection_prompt(direction, strategy, backtest_result)

        if knowledge_context:
            prompt = prompt + f"\n\n## 参考知识:\n{knowledge_context}"

        response = self.llm_service.invoke(prompt, format="json")

        content = response.content.strip()
        if self.logger:
            self.logger.info(f"[ToT反思] {direction} 分支 LLM 响应: {content[:80]}")

        result = parse_llm_json(content)
        return result

    def _evaluate_branches(  # noqa: PLR0912
        self, branches: list[dict[str, Any]], backtest_result: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """评估各分支的改进建议"""
        metrics = backtest_result.get("metrics", {})

        for branch in branches:
            score = 0
            direction = branch.get("direction", "")

            if direction == "收益增强":
                return_rate = metrics.get("return", 0) or metrics.get(
                    "annual_return", 0
                )
                if return_rate < 0:
                    score += 30
                elif return_rate < _MIN_RETURN_THRESHOLD:
                    score += 15
                else:
                    score += 5

            elif direction == "风险控制":
                max_drawdown = abs(
                    metrics.get("max_drawdown", 0) or metrics.get("drawdown", 0)
                )
                if max_drawdown > _DRAWDOWN_RISK_THRESHOLD:
                    score += 30
                elif max_drawdown > _DRAWDOWN_MODERATE_THRESHOLD:
                    score += 15
                else:
                    score += 5

            elif direction == "收益稳定性":
                sharpe = metrics.get("sharpe_ratio", 0) or metrics.get("sharpe", 0)
                if sharpe < _POOR_SHARPE_THRESHOLD:
                    score += 30
                elif sharpe < _MIN_SHARPE_THRESHOLD:
                    score += 15
                else:
                    score += 5

            branch["score"] = score

        return sorted(branches, key=lambda x: x["score"], reverse=True)

    def reflect_with_tot(
        self, strategy: dict[str, Any], backtest_result: dict[str, Any]
    ) -> dict[str, Any]:
        """使用思维树(ToT)模型进行多分支反思"""
        branches = []

        for direction in OPTIMIZATION_DIRECTIONS:
            try:
                result = self._run_branch_reflection(
                    direction, strategy, backtest_result
                )
                branches.append(
                    {
                        "direction": direction,
                        "reflection": result.get("reflection", ""),
                        "improvement_suggestions": result.get(
                            "improvement_suggestions", []
                        ),
                    }
                )
                if self.logger:
                    self.logger.info(f"ToT 分支 {direction} 完成")
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"ToT 分支 {direction} 执行失败：{e}")
                continue

        if not branches:
            raise ValueError("所有 ToT 分支均失败")

        evaluated_branches = self._evaluate_branches(branches, backtest_result)

        best_branch = evaluated_branches[0]

        return {
            "reflection": best_branch["reflection"],
            "improvement_suggestions": best_branch["improvement_suggestions"],
            "explored_paths": evaluated_branches,
            "selected_direction": best_branch["direction"],
            "tot_enabled": True,
        }

    def _simple_fallback(
        self,
        strategy: dict[str, Any],  # noqa: ARG002
        backtest_result: dict[str, Any],
    ) -> dict[str, Any]:
        """极简兜底 - 基于规则的通用建议"""
        metrics = backtest_result.get("metrics", {})
        if not metrics:
            return {
                "reflection": "无法获取回测指标",
                "improvement_suggestions": ["建议检查回测配置是否正确"],
                "tot_enabled": False,
            }

        suggestions = []
        return_rate = metrics.get("return", 0) or metrics.get("annual_return", 0)
        max_drawdown = abs(metrics.get("max_drawdown", 0) or metrics.get("drawdown", 0))
        sharpe = metrics.get("sharpe_ratio", 0) or metrics.get("sharpe", 0)

        if max_drawdown > _DRAWDOWN_MODERATE_THRESHOLD:
            suggestions.append("建议添加止损机制或降低仓位以控制回撤")
        if sharpe < _MIN_SHARPE_THRESHOLD:
            suggestions.append("建议优化因子权重以提升风险调整收益")
        if return_rate < _MIN_RETURN_THRESHOLD:
            suggestions.append("建议扩展选股池或增加有效因子")

        if not suggestions:
            suggestions.append("当前策略表现良好，建议小幅优化参数")

        primary_issue = self._identify_primary_issue(backtest_result)

        return {
            "reflection": f"简化分析：主要问题为 {primary_issue}，回测指标 return={return_rate:.2f}%, max_drawdown={max_drawdown:.2f}%, sharpe={sharpe:.2f}",
            "improvement_suggestions": suggestions,
            "primary_issue": primary_issue,
            "tot_enabled": False,
        }

    def reflect(
        self, strategy: dict[str, Any], backtest_result: dict[str, Any]
    ) -> dict[str, Any]:
        """反思 - 分析回测结果并生成改进建议（支持 ToT 模式）"""
        try:
            if self.logger:
                self.logger.info("开始 ToT 多分支反思")
            return self.reflect_with_tot(strategy, backtest_result)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"ToT 反思失败，使用极简 fallback: {e}")
            return self._simple_fallback(strategy, backtest_result)

    def optimize_strategy(
        self, strategy: dict[str, Any], improvement_suggestions: list
    ) -> dict[str, Any]:
        """优化策略 - 根据改进建议优化策略"""

        suggestions_str = "\n".join([f"- {s}" for s in improvement_suggestions])
        knowledge_context = self._get_research_context(
            "策略优化方法", node_type="optimize"
        )

        prompt = strategy_optimize_prompt.format(
            strategy=strategy,
            suggestions_text=suggestions_str,
            backtest_history="无",
            market_characteristics=knowledge_context if knowledge_context else "无",
        )
        response = self.llm_service.invoke(prompt)

        optimized = strategy.copy()
        optimized["description"] = response.content
        optimized["optimized"] = True
        if self.logger:
            self.logger.info("策略优化完成")

        return optimized
