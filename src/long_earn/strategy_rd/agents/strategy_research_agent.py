import json
from typing import Any, Dict, List, Optional

from long_earn.utils.logger import LOGGER
from long_earn.utils.llm_factory import create_llm

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


class StrategyResearchAgent:
    """策略研究智能体"""

    def __init__(
        self, llm_type: str = "ollama", model_name: str = "", base_url: str = ""
    ):
        self.llm_type = llm_type
        self.model_name = model_name
        self.base_url = base_url
        self._knowledge_cache: Dict[str, List[str]] = {}

    def _create_llm(self):
        return create_llm(
            llm_type=self.llm_type or "ollama",
            model_name=self.model_name or "qwen3.5:9b",
            base_url=self.base_url or "http://localhost:11434",
        )

    def _search_knowledge(
        self,
        query: str,
        categories: Optional[List[str]] = None,
        terms: Optional[List[str]] = None,
    ) -> List[str]:
        """搜索知识库获取相关参考信息"""
        try:
            from long_earn.tools.store import search_knowledge

            results = search_knowledge(query, k=3, categories=categories, terms=terms)
            return results
        except Exception as e:
            LOGGER.warning(f"搜索知识库失败: {e}")
            return []

    def _get_knowledge_context(
        self,
        query: str,
        node_type: Optional[str] = None,
    ) -> str:
        """获取知识库上下文，如果缓存中没有则搜索

        Args:
            query: 搜索查询
            node_type: 节点类型，可选 "research", "reflection", "optimize"
        """
        cache_key = f"{node_type}:{query}" if node_type else query

        if cache_key in self._knowledge_cache:
            return "\n".join(self._knowledge_cache[cache_key])

        categories = NODE_CATEGORIES.get(node_type, None) if node_type else None

        results = self._search_knowledge(query, categories=categories)
        if results:
            self._knowledge_cache[cache_key] = results
            return "\n".join(results)
        return ""

    def research_strategy(self, query: str) -> Dict[str, Any]:
        """研究策略 - 根据用户查询生成初始策略"""
        from langchain_core.prompts import ChatPromptTemplate

        from .strategy_research_prompt import create_strategy_research_prompt

        llm = self._create_llm()

        knowledge_context = self._get_knowledge_context(query, node_type="research")

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
        response = llm.invoke(messages)
        LOGGER.info(f"策略研究代理生成策略完成：{query}")

        return {
            "strategy_name": "研究策略",
            "description": response.content,
            "query": query,
        }

    def _identify_primary_issue(self, backtest_result: Dict[str, Any]) -> str:
        """根据回测指标自动判断主要问题方向"""
        metrics = backtest_result.get("metrics", {})
        if not metrics:
            return "收益增强"

        return_rate = metrics.get("return", 0) or metrics.get("annual_return", 0)
        max_drawdown = abs(metrics.get("max_drawdown", 0) or metrics.get("drawdown", 0))
        sharpe = metrics.get("sharpe_ratio", 0) or metrics.get("sharpe", 0)

        if max_drawdown > 30:
            return "风险控制"
        elif return_rate < 0:
            return "收益增强"
        elif sharpe < 0.5:
            return "收益稳定性"
        else:
            return "收益增强"

    def _build_reflection_prompt(
        self, direction: str, strategy: Dict[str, Any], backtest_result: Dict[str, Any]
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
{direction_config['focus']}
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
        self, direction: str, strategy: Dict[str, Any], backtest_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """运行单个方向的反思"""
        llm = self._create_llm()

        knowledge_context = self._get_knowledge_context(
            f"策略{direction}方法", node_type="reflection"
        )
        prompt = self._build_reflection_prompt(direction, strategy, backtest_result)

        if knowledge_context:
            prompt = prompt + f"\n\n## 参考知识:\n{knowledge_context}"

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
        return result

    def _evaluate_branches(
        self, branches: List[Dict[str, Any]], backtest_result: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
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
                elif return_rate < 10:
                    score += 15
                else:
                    score += 5

            elif direction == "风险控制":
                max_drawdown = abs(
                    metrics.get("max_drawdown", 0) or metrics.get("drawdown", 0)
                )
                if max_drawdown > 30:
                    score += 30
                elif max_drawdown > 20:
                    score += 15
                else:
                    score += 5

            elif direction == "收益稳定性":
                sharpe = metrics.get("sharpe_ratio", 0) or metrics.get("sharpe", 0)
                if sharpe < 0.3:
                    score += 30
                elif sharpe < 0.5:
                    score += 15
                else:
                    score += 5

            branch["score"] = score

        return sorted(branches, key=lambda x: x["score"], reverse=True)

    def reflect_with_tot(
        self, strategy: Dict[str, Any], backtest_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """使用思维树(ToT)模型进行多分支反思"""
        branches = []

        for direction in OPTIMIZATION_DIRECTIONS.keys():
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
                LOGGER.info(f"ToT 分支 {direction} 完成")
            except Exception as e:
                LOGGER.warning(f"ToT 分支 {direction} 执行失败: {e}")
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
        self, strategy: Dict[str, Any], backtest_result: Dict[str, Any]
    ) -> Dict[str, Any]:
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

        if max_drawdown > 20:
            suggestions.append("建议添加止损机制或降低仓位以控制回撤")
        if sharpe < 0.5:
            suggestions.append("建议优化因子权重以提升风险调整收益")
        if return_rate < 10:
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
        self, strategy: Dict[str, Any], backtest_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """反思 - 分析回测结果并生成改进建议（支持 ToT 模式）"""
        try:
            LOGGER.info("开始 ToT 多分支反思")
            return self.reflect_with_tot(strategy, backtest_result)
        except Exception as e:
            LOGGER.warning(f"ToT 反思失败，使用极简 fallback: {e}")
            return self._simple_fallback(strategy, backtest_result)

    def optimize_strategy(
        self, strategy: Dict[str, Any], improvement_suggestions: list
    ) -> Dict[str, Any]:
        """优化策略 - 根据改进建议优化策略"""
        from .strategy_research_prompt import strategy_optimize_prompt

        llm = self._create_llm()

        suggestions_str = "\n".join([f"- {s}" for s in improvement_suggestions])
        knowledge_context = self._get_knowledge_context(
            "策略优化方法", node_type="optimize"
        )

        prompt = strategy_optimize_prompt.format(
            strategy=strategy,
            suggestions_text=suggestions_str,
            backtest_history="无",
            market_characteristics=knowledge_context if knowledge_context else "无",
        )
        response = llm.invoke(prompt)

        optimized = strategy.copy()
        optimized["description"] = response.content
        optimized["optimized"] = True
        LOGGER.info("策略优化完成")

        return optimized
