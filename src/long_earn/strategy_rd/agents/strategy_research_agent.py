import json
from typing import TYPE_CHECKING, Any

from long_earn.core.llm_utils import parse_llm_json
from long_earn.core.prompt_loader import MarkdownPromptTemplate
from long_earn.strategy_rd.agents.mixins import KnowledgeContextMixin

from .strategy_research_prompt import (
    create_strategy_research_prompt,
    render_strategy_optimize_prompt,
)

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext
    from long_earn.skills.personas.protocol import PersonaResult

# 单位语义统一：所有阈值与 BacktestResult 字段一致使用"小数"（return/drawdown 不带 %）
# 历史上这里曾把 drawdown / return 阈值写成百分比（30、20、10），与扁平 backtest_result
# 的小数指标不匹配，导致 fallback 与 ToT 评判几乎永远不触发——属于隐蔽的"单位错位"。
_DRAWDOWN_RISK_THRESHOLD = 0.30
_DRAWDOWN_MODERATE_THRESHOLD = 0.20
_MIN_SHARPE_THRESHOLD = 0.5
_POOR_SHARPE_THRESHOLD = 0.3
_MIN_RETURN_THRESHOLD = 0.10

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
    "策略家族切换": {
        "focus": "当前因子族（如动量）在长周期亏损而近期盈利，可能已失效，应转向异族因子",
        "metrics": ["family_long_term_return"],
        "typical_improvements": [
            "换为均值回归因子（RSI 超卖反转）",
            "引入价值因子（ROE/毛利率筛选）",
            "成交量加权因子",
            "波动率反转因子",
            "用算子路径（operator_factors）表达滚动窗口多因子",
        ],
        "categories": ["二、技术分析类", "三、财务分析类", "五、量化策略类"],
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
        self.llm_service = context.require_llm()
        self.memory = context.require_memory()
        self.logger = context.logger
        self._knowledge_cache: dict[str, list[str]] = {}
        self._event_cache: dict[str, list[str]] = {}

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
        response = self.llm_service.invoke(prompt)
        if self.logger:
            self.logger.info(f"策略研究代理生成策略完成：{query}")

        return {
            "strategy_name": "研究策略",
            "description": response.content,
            "query": query,
        }

    def research_strategy_with_context(
        self,
        query: str,
        knowledge_context: str = "",
        master_hints: "dict[str, PersonaResult] | None" = None,
    ) -> dict[str, Any]:
        """使用已有上下文的研究策略。

        Args:
            query: 用户研究需求
            knowledge_context: 已检索到的知识上下文
            master_hints: name -> PersonaResult 映射，由 _research_node
                调用 4 个大师 strategy_generate mode 得到。None 或空 dict 时
                行为与原 research_strategy_with_context 完全一致（向后兼容）。
        """

        if self.logger:
            self.logger.info(f"[策略研究Agent] 开始研究: {query}")

        master_hints_context = self._format_master_hints(master_hints)
        if master_hints_context and self.logger:
            self.logger.info(
                f"[策略研究Agent] 注入大师策略生成建议: {len(master_hints or {})} 位"
            )

        prompt = create_strategy_research_prompt(
            target_market="stock",
            query=query,
            strategy_examples="无",
            strategy_context=knowledge_context if knowledge_context else "无",
            master_hints_context=master_hints_context,
        )

        response = self.llm_service.invoke(prompt)
        if self.logger:
            self.logger.info("策略研究代理生成策略完成（使用自适应检索上下文）")

        return {
            "strategy_name": "研究策略",
            "description": response.content,
            "query": query,
        }

    def _format_master_hints(
        self, master_hints: "dict[str, PersonaResult] | None"
    ) -> str:
        """把大师策略生成建议格式化为 prompt 用的可读文本段落。

        每个大师一段，结构：
            ## <大师名>建议
            裁决: <verdict>
            置信度: <confidence>
            建议:
              - <suggestion1>
              - <suggestion2>
            依据: <rationale>

        Args:
            master_hints: name -> PersonaResult 映射；None 或空 dict
                时返回空串（保持向后兼容，prompt 不出现 master_hints 字样）。

        Returns:
            可读文本段落；无大师建议时返回 ""。
        """
        if not master_hints:
            return ""

        sections: list[str] = []
        for name, hint in master_hints.items():
            # 兼容 PersonaResult 实例与 dict 两种形式
            if hasattr(hint, "verdict"):
                verdict = hint.verdict
                rationale = hint.rationale
                suggestions = hint.suggestions
                confidence = hint.confidence
                display_name = getattr(hint, "display_name", None) or name
            elif isinstance(hint, dict):
                verdict = hint.get("verdict", "未知")
                rationale = hint.get("rationale", "")
                suggestions = hint.get("suggestions", []) or []
                confidence = hint.get("confidence", 0.0)
                display_name = hint.get("display_name", name)
            else:
                continue

            suggestions_str = (
                "\n".join(f"  - {s}" for s in suggestions) if suggestions else "  - 无"
            )
            sections.append(
                f"\n\n## {display_name}建议\n"
                f"裁决: {verdict}\n"
                f"置信度: {confidence}\n"
                f"建议:\n{suggestions_str}\n"
                f"依据: {rationale}"
            )

        return "".join(sections)

    def _identify_primary_issue(self, backtest_result: dict[str, Any]) -> str:
        """根据回测指标自动判断主要问题方向

        兼容两种结构：
        - 扁平结构（BacktestServiceImpl.run 实际返回）：
          {"total_return": ..., "sharpe_ratio": ..., "max_drawdown": ...}
        - 嵌套结构（_backtest_node 在 engine_error 时填的占位）：
          {"metrics": {"return": ..., "sharpe_ratio": ..., "max_drawdown": ...}}

        如果回测失败（带 error 字段），仍然给出方向以便上层流程继续。
        """
        # 优先从扁平字段读取
        return_rate = backtest_result.get("total_return")
        sharpe = backtest_result.get("sharpe_ratio")
        max_drawdown = backtest_result.get("max_drawdown")

        # 回退到嵌套 metrics 字段（兼容历史调用方）
        if return_rate is None or sharpe is None or max_drawdown is None:
            metrics = backtest_result.get("metrics", {}) or {}
            return_rate = (
                return_rate
                if return_rate is not None
                else (metrics.get("return") or metrics.get("annual_return") or 0)
            )
            sharpe = (
                sharpe
                if sharpe is not None
                else (metrics.get("sharpe_ratio") or metrics.get("sharpe") or 0)
            )
            max_drawdown = (
                max_drawdown
                if max_drawdown is not None
                else (metrics.get("max_drawdown") or metrics.get("drawdown") or 0)
            )

        return_rate = return_rate or 0
        sharpe = sharpe or 0
        max_drawdown = abs(max_drawdown or 0)

        if max_drawdown > _DRAWDOWN_RISK_THRESHOLD:
            return "风险控制"
        if return_rate < 0:
            return "收益增强"
        if sharpe < _MIN_SHARPE_THRESHOLD:
            return "收益稳定性"
        return "收益增强"

    def _build_reflection_prompt(
        self,
        direction: str,
        strategy: dict[str, Any],
        backtest_result: dict[str, Any],
        master_context: str = "",
        history_return: float = 0.0,
    ) -> str:
        """构建特定方向的反思提示

        Args:
            direction: 反思方向（收益增强/风险控制/收益稳定性/策略家族切换）
            strategy: 当前策略字典
            backtest_result: 回测结果字典
            master_context: 大师视角的可读文本段落，非空时作为补充
                上下文注入 prompt；为空时与原行为完全一致（向后兼容）。
            history_return: 历史窗口收益率。对"策略家族切换"方向注入
                "长周期亏损"语义；其他方向仅在 < 0 时附加提示。
        """
        direction_config = OPTIMIZATION_DIRECTIONS.get(
            direction, OPTIMIZATION_DIRECTIONS["收益增强"]
        )

        # 大师视角相关段落（仅在非空时注入，保持向后兼容）
        if master_context:
            master_section = (
                f"\n\n<master_perspectives>\n{master_context}\n</master_perspectives>"
            )
            framework_extra = (
                "\n\n若上方 <master_perspectives> 提供了投资大师的审视视角，"
                "请在反思中综合参考其裁决、弱点与建议，"
                "但最终改进方案仍以量化数据为依据。"
            )
            thinking_master_step = (
                "\n3. 综合大师视角识别盲点\n4. 提出具体可执行的改进方案"
            )
        else:
            master_section = ""
            framework_extra = ""
            thinking_master_step = "\n3. 提出具体可执行的改进方案"

        # 家族失效信号注入：历史窗口亏损时提示因子族可能失效
        history_section = ""
        if direction == "策略家族切换":
            history_section = (
                f"\n\n<family_failure_signal>\n"
                f"历史窗口收益率: {history_return:.4f}（{'亏损' if history_return < 0 else '盈利'}）\n"
                f"近期窗口收益见上方回测结果。\n"
                f"若历史窗口亏损而近期盈利，说明当前因子族（如动量）可能仅在特定市场环境短期有效，"
                f"长周期不具稳健性。应在改进建议中提出转向异族因子：\n"
                f"- 均值回归：RSI 超卖反转、偏离均线回归\n"
                f"- 价值因子：ROE/毛利率/净利润增长筛选\n"
                f"- 成交量异动：放量突破、缩量洗盘\n"
                f"- 波动率反转：低波动率因子\n"
                f"- 多因子复合：用算子路径（operator_factors）的 windowed 算子实现滚动窗口\n"
                f"</family_failure_signal>"
            )
        elif history_return < 0:
            history_section = (
                f"\n\n<note>\n"
                f"注意：历史窗口收益率为 {history_return:.4f}（亏损），"
                f"当前策略在长周期可能不稳健，反思时应考虑因子族失效可能性。\n"
                f"</note>"
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
</focus>{master_section}{history_section}

<analysis_framework>
请从以下维度进行{direction}分析：

1. 当前策略在{direction}方面的表现
2. 存在的主要问题及原因
3. 可行的改进方案
4. 预期改进效果{framework_extra}
</analysis_framework>

<thinking_process>
在给出建议前，请按步骤思考：
1. 首先识别回测结果中的关键指标
2. 分析当前策略在{direction}方面的问题根源{thinking_master_step}
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
        self,
        direction: str,
        strategy: dict[str, Any],
        backtest_result: dict[str, Any],
        master_context: str = "",
        history_return: float = 0.0,
    ) -> dict[str, Any]:
        """运行单个方向的反思"""
        if self.logger:
            self.logger.info(f"[ToT反思] 开始分支: {direction}")

        knowledge_context = self._get_research_context(
            f"策略{direction}方法", node_type="reflection"
        )
        prompt = self._build_reflection_prompt(
            direction,
            strategy,
            backtest_result,
            master_context=master_context,
            history_return=history_return,
        )

        if knowledge_context:
            prompt = prompt + f"\n\n## 参考知识:\n{knowledge_context}"

        response = self.llm_service.invoke(prompt, format="json")

        content = response.content.strip()
        if self.logger:
            self.logger.info(f"[ToT反思] {direction} 分支 LLM 响应: {content[:80]}")

        result = parse_llm_json(content)
        return result

    @staticmethod
    def _read_metric(
        backtest_result: dict[str, Any],
        flat_key: str,
        nested_keys: tuple[str, ...] = (),
        default: float = 0.0,
    ) -> float:
        """从 backtest_result 同时兼容扁平与嵌套结构读取单个数值指标

        - 扁平结构（BacktestServiceImpl.run 实际返回）：顶层 flat_key
        - 嵌套结构（_backtest_node 在 engine_error 时填的占位 metrics）：metrics[flat_key]
          以及若干历史别名（nested_keys，如 "return" 别名 "annual_return"）
        """
        v = backtest_result.get(flat_key)
        if v is not None:
            return float(v)
        metrics = backtest_result.get("metrics", {}) or {}
        v = metrics.get(flat_key)
        if v is not None:
            return float(v)
        for nk in nested_keys:
            v = metrics.get(nk)
            if v is not None:
                return float(v)
        return default

    def _evaluate_branches(  # noqa: PLR0912
        self,
        branches: list[dict[str, Any]],
        backtest_result: dict[str, Any],
        history_return: float = 0.0,
    ) -> list[dict[str, Any]]:
        """评估各分支的改进建议

        必须兼容扁平与嵌套两种结构（见 `_read_metric`），否则扁平结构下
        所有分支都得到默认 +5 分（最低档），sorted 稳定排序导致 ToT 永远
        选 OPTIMIZATION_DIRECTIONS 第一个键作为 best_branch——多分支退化为单一分支。

        家族切换打分：当 ``history_return < 0``（长周期亏损）且近期收益为正时，
        给"策略家族切换"分支加 40 分（高于其他分支 30 分上限），使其优先被选中。
        """
        recent_return = self._read_metric(
            backtest_result, "total_return", ("return", "annual_return")
        )
        for branch in branches:
            score = 0
            direction = branch.get("direction", "")

            if direction == "收益增强":
                return_rate = self._read_metric(
                    backtest_result, "total_return", ("return", "annual_return")
                )
                if return_rate < 0:
                    score += 30
                elif return_rate < _MIN_RETURN_THRESHOLD:
                    score += 15
                else:
                    score += 5

            elif direction == "风险控制":
                max_drawdown = abs(
                    self._read_metric(backtest_result, "max_drawdown", ("drawdown",))
                )
                if max_drawdown > _DRAWDOWN_RISK_THRESHOLD:
                    score += 30
                elif max_drawdown > _DRAWDOWN_MODERATE_THRESHOLD:
                    score += 15
                else:
                    score += 5

            elif direction == "收益稳定性":
                sharpe = self._read_metric(backtest_result, "sharpe_ratio", ("sharpe",))
                if sharpe < _POOR_SHARPE_THRESHOLD:
                    score += 30
                elif sharpe < _MIN_SHARPE_THRESHOLD:
                    score += 15
                else:
                    score += 5

            elif direction == "策略家族切换":
                # 家族失效检测：历史窗口亏损 + 当前策略退化/亏损
                # 长周期亏损 + 近期盈利 = 因子族失效信号，最高优先级
                if history_return < 0 and recent_return > 0:
                    score += 40
                elif history_return < 0 and recent_return <= 0:
                    # 长周期亏损 + 近期也亏损/退化 = 因子族彻底失效，
                    # 应优先换家族而非在原因子族内调参，得分高于"收益增强"(30)
                    score += 35
                elif history_return < 0:
                    score += 25
                else:
                    score += 5

            branch["score"] = score

        return sorted(branches, key=lambda x: x["score"], reverse=True)

    def reflect_with_tot(
        self,
        strategy: dict[str, Any],
        backtest_result: dict[str, Any],
        master_context: str = "",
        history_return: float = 0.0,
    ) -> dict[str, Any]:
        """使用思维树(ToT)模型进行多分支反思

        Args:
            strategy: 当前策略字典
            backtest_result: 回测结果字典
            master_context: 大师视角可读文本段落，非空时注入各分支 prompt；
                为空时与原 ToT 行为完全一致（向后兼容）。
            history_return: 历史窗口收益率（家族失效检测信号）。
                < 0 表示当前因子族在长周期亏损，可能已失效。
        """
        branches = []

        for direction in OPTIMIZATION_DIRECTIONS:
            try:
                result = self._run_branch_reflection(
                    direction,
                    strategy,
                    backtest_result,
                    master_context=master_context,
                    history_return=history_return,
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

        evaluated_branches = self._evaluate_branches(
            branches, backtest_result, history_return=history_return
        )

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
        """极简兜底 - 基于规则的通用建议

        必须兼容扁平结构（BacktestServiceImpl.run 实际返回）和嵌套 metrics 结构，
        否则 ToT 异常进 fallback 时会陷入"无法获取回测指标"死分支，
        导致 reflection 完全失去回测信息引导，拖垮整轮演进。
        """
        return_rate = backtest_result.get("total_return")
        sharpe = backtest_result.get("sharpe_ratio")
        max_drawdown = backtest_result.get("max_drawdown")

        # 回退到嵌套 metrics 字段
        if return_rate is None or sharpe is None or max_drawdown is None:
            metrics = backtest_result.get("metrics", {}) or {}
            return_rate = (
                return_rate
                if return_rate is not None
                else (metrics.get("return") or metrics.get("annual_return"))
            )
            sharpe = (
                sharpe
                if sharpe is not None
                else (metrics.get("sharpe_ratio") or metrics.get("sharpe"))
            )
            max_drawdown = (
                max_drawdown
                if max_drawdown is not None
                else (metrics.get("max_drawdown") or metrics.get("drawdown"))
            )

        # 三项核心指标全部缺失才认定"无法获取"——只要任一项有真实值就要用上
        if return_rate is None and sharpe is None and max_drawdown is None:
            return {
                "reflection": "无法获取回测指标",
                "improvement_suggestions": ["建议检查回测配置是否正确"],
                "tot_enabled": False,
            }

        return_rate = return_rate or 0
        sharpe = sharpe or 0
        max_drawdown_abs = abs(max_drawdown or 0)

        suggestions: list[str] = []
        if max_drawdown_abs > _DRAWDOWN_MODERATE_THRESHOLD:
            suggestions.append("建议添加止损机制或降低仓位以控制回撤")
        if sharpe < _MIN_SHARPE_THRESHOLD:
            suggestions.append("建议优化因子权重以提升风险调整收益")
        if return_rate < _MIN_RETURN_THRESHOLD:
            suggestions.append("建议扩展选股池或增加有效因子")

        if not suggestions:
            suggestions.append("当前策略表现良好，建议小幅优化参数")

        primary_issue = self._identify_primary_issue(backtest_result)

        return {
            "reflection": (
                f"简化分析：主要问题为 {primary_issue}，"
                f"回测指标 return={return_rate:.2f}, "
                f"max_drawdown={max_drawdown_abs:.2f}, sharpe={sharpe:.2f}"
            ),
            "improvement_suggestions": suggestions,
            "primary_issue": primary_issue,
            "tot_enabled": False,
        }

    def _format_master_perspectives(
        self, master_perspectives: "dict[str, PersonaResult] | None"
    ) -> str:
        """把大师审视结果格式化为 prompt 用的可读文本段落。

        每个大师一段，结构：
            ## <大师名>（<视角>）
            裁决: <verdict>
            置信度: <confidence>
            弱点: <weaknesses 列表>
            建议: <suggestions 列表>
            依据: <rationale>

        Args:
            master_perspectives: name -> PersonaResult 映射；None 或空 dict
                时返回空串（保持向后兼容）。

        Returns:
            可读文本段落；无大师视角时返回 ""
        """
        if not master_perspectives:
            return ""

        sections: list[str] = []
        for name, view in master_perspectives.items():
            # 兼容 PersonaResult 实例与 dict 两种形式
            if hasattr(view, "verdict"):
                verdict = view.verdict
                rationale = view.rationale
                weaknesses = view.weaknesses
                suggestions = view.suggestions
                confidence = view.confidence
                display_name = getattr(
                    view, "display_name", None
                ) or name
                perspective = getattr(view, "perspective", None) or ""
            elif isinstance(view, dict):
                verdict = view.get("verdict", "未知")
                rationale = view.get("rationale", "")
                weaknesses = view.get("weaknesses", []) or []
                suggestions = view.get("suggestions", []) or []
                confidence = view.get("confidence", 0.0)
                display_name = view.get("display_name", name)
                perspective = view.get("perspective", "")
            else:
                continue

            title = f"## {display_name}（{perspective}）" if perspective else f"## {display_name}"
            weaknesses_str = (
                "\n".join(f"  - {w}" for w in weaknesses) if weaknesses else "  - 无"
            )
            suggestions_str = (
                "\n".join(f"  - {s}" for s in suggestions) if suggestions else "  - 无"
            )
            sections.append(
                f"{title}\n"
                f"裁决: {verdict}\n"
                f"置信度: {confidence}\n"
                f"弱点:\n{weaknesses_str}\n"
                f"建议:\n{suggestions_str}\n"
                f"依据: {rationale}"
            )

        return "\n\n".join(sections)

    def reflect(
        self,
        strategy: dict[str, Any],
        backtest_result: dict[str, Any],
        master_perspectives: "dict[str, PersonaResult] | None" = None,
        history_return: float = 0.0,
    ) -> dict[str, Any]:
        """反思 - 分析回测结果并生成改进建议（支持 ToT 模式 + 大师视角）

        Args:
            strategy: 当前策略字典
            backtest_result: 回测结果字典
            master_perspectives: name -> PersonaResult 映射，由 _reflection_node
                调用 4 个大师 strategy_review mode 得到。None 或空 dict 时
                行为与原 reflect 完全一致（向后兼容）。
            history_return: 历史窗口收益率（家族失效检测信号）。
                < 0 时 ToT 会优先考虑"策略家族切换"分支。
        """
        master_context = self._format_master_perspectives(master_perspectives)
        if master_context and self.logger:
            self.logger.info(
                f"[反思] 注入大师视角: {len(master_perspectives or {})} 位"
            )
        try:
            if self.logger:
                self.logger.info("开始 ToT 多分支反思")
            return self.reflect_with_tot(
                strategy,
                backtest_result,
                master_context=master_context,
                history_return=history_return,
            )
        except Exception as e:
            if self.logger:
                self.logger.warning(f"ToT 反思失败，使用极简 fallback: {e}")
            return self._simple_fallback(strategy, backtest_result)

    @staticmethod
    def _format_previous_backtest(previous_backtest: dict[str, Any] | None) -> str:
        """把上一轮回测结果格式化为 prompt 用的可读段落。

        - 优先扁平字段，回退到嵌套 metrics 子字典
        - metrics_unreliable 或带 error 时显式提示，避免 LLM 把占位 0 当真业绩
        """
        if not previous_backtest:
            return "无"

        unreliable = bool(previous_backtest.get("metrics_unreliable")) or bool(
            previous_backtest.get("error")
        )
        if unreliable:
            return (
                f"上一轮回测失败/数据不足（{previous_backtest.get('error', '占位指标')}），"
                "请仅依据改进建议进行结构性优化，不要将占位 0 当作真实业绩。"
            )

        metric_keys = (
            "total_return",
            "annual_return",
            "sharpe_ratio",
            "max_drawdown",
            "volatility",
            "win_rate",
            "trading_days",
        )
        nested = previous_backtest.get("metrics", {}) or {}
        lines: list[str] = []
        for k in metric_keys:
            v = previous_backtest.get(k)
            if v is None:
                v = nested.get(k)
            if v is not None:
                lines.append(f"  - {k}: {v}")
        return "上一轮回测指标：\n" + "\n".join(lines) if lines else "无"

    def _retrieve_past_experience(self, strategy: dict[str, Any]) -> str:
        """从记忆系统检索同类策略的历史经验，构造 prompt 段落。

        检索策略：依次尝试多个查询词，取第一个有结果的。
        原因：RetrievalIndex 的 keyword 通道用 jieba 分词，对英文策略名 +
        英文因子名混合的查询支持差（如 "MomentumVolROEStrategy momentum_20"
        返回 0 条），但用单一中文关键词（如"动量"、"策略"）能命中。
        """
        strategy_name = strategy.get("strategy_name", "") or ""
        factors = strategy.get("factors_used", []) or []
        factor_str = " ".join(str(f) for f in factors) if isinstance(factors, list) else ""

        # 查询词候选：从最具体到最通用，依次尝试
        # 中文翻译基于常见因子命名（momentum→动量、volatility→波动率、roe→roe）
        candidate_queries = [
            "动量策略",
            "策略经验",
            strategy_name,
            factor_str,
            "策略优化",
        ]
        # 去重 + 去空
        seen: set[str] = set()
        queries = [q for q in candidate_queries if q and q not in seen and not seen.add(q)]

        past: list = []
        try:
            for q in queries:
                past = self.memory.search_experience(query=q, k=2)
                if past:
                    if self.logger:
                        self.logger.debug(
                            f"检索历史经验命中（query={q!r}, 返回 {len(past)} 条）"
                        )
                    break
        except Exception as exc:
            if self.logger:
                self.logger.warning(f"检索历史经验失败: {exc}")
            return ""
        if not past:
            return ""
        lines = [
            f"- {p.name}: metrics={p.metrics}" for p in past
        ]
        return "历史同类经验：\n" + "\n".join(lines)

    def optimize_strategy(
        self,
        strategy: dict[str, Any],
        improvement_suggestions: list,
        previous_backtest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """优化策略 - 根据改进建议优化策略

        相对于初版的两点关键改造：
        1. 把当前回测结果 `previous_backtest` 注入 prompt 的 backtest_history，
           让 LLM 真正基于"当前业绩"提优化方案，而不是看着 "无" 凭空想象。
        2. 通过 memory.search_experience 检索同类策略的历史经验，
           注入 market_characteristics 字段——让记忆系统真正参与策略改进。
        """
        suggestions_str = "\n".join([f"- {s}" for s in improvement_suggestions])
        knowledge_context = self._get_research_context(
            "策略优化方法", node_type="optimize"
        )
        backtest_history = self._format_previous_backtest(previous_backtest)
        memory_section = self._retrieve_past_experience(strategy)
        market_characteristics = (
            "\n\n".join(filter(None, [knowledge_context or "", memory_section])) or "无"
        )

        prompt = render_strategy_optimize_prompt(
            strategy=strategy,
            suggestions_text=suggestions_str,
            backtest_history=backtest_history,
            market_characteristics=market_characteristics,
        )
        response = self.llm_service.invoke(prompt)

        optimized = strategy.copy()
        optimized["description"] = response.content
        optimized["optimized"] = True
        # 记录演进谱系：方便审计 / 后续 reflection 引用
        lineage = list(strategy.get("evolution_lineage", []) or [])
        lineage.append(
            {
                "from": strategy.get("strategy_name", "unknown"),
                "suggestions_count": len(improvement_suggestions),
                "had_backtest": previous_backtest is not None,
            }
        )
        optimized["evolution_lineage"] = lineage
        if self.logger:
            self.logger.info(
                f"策略优化完成（演进深度={len(lineage)}, "
                f"历史经验注入={'是' if memory_section else '否'}）"
            )

        return optimized

    # ── HTR 六步循环方法（ADR-010 Phase 2）──────────────────────

    def observe(self, tree_snapshot: dict[str, Any]) -> dict[str, Any]:
        """观察阶段 — 分析当前研究状态，识别弱点和下一步方向。

        ADR-014 任务2：``tree_snapshot["related_concepts"]`` 由 HTR 的
        ``_observe_node`` 通过 Connector 图谱查询填入，非空时 LLM 能拿到
        结构化图谱视角（关联因子族 / 历史失败案例）。
        """
        prompt_template = MarkdownPromptTemplate(
            "observe_prompt.md",
            [
                "current_best",
                "frontier",
                "ancestor_insights",
                "pruned_directions",
                "related_concepts",
            ],
            __file__,
        )
        prompt = prompt_template.format(
            current_best=tree_snapshot.get("current_best", "无"),
            frontier=tree_snapshot.get("frontier", "无"),
            ancestor_insights=tree_snapshot.get("ancestor_insights", "无"),
            pruned_directions=tree_snapshot.get("pruned_directions", "无"),
            related_concepts=tree_snapshot.get("related_concepts", "无"),
        )
        response = self.llm_service.invoke(prompt)
        # LLM 偶发返回空内容或非 JSON 时容错：返回默认观察让循环继续，
        # 而非抛 JSONDecodeError 让整轮 HTR 崩溃。
        default_obs = {"next_focus": "继续优化因子组合与风控参数，关注近期回测暴露的问题"}
        result = parse_llm_json(response.content, default=default_obs)
        if self.logger:
            self.logger.info(f"[HTR-观察] {result.get('next_focus', '未知')}")
        return result if isinstance(result, dict) else {"observations": str(result)}

    def ideate(  # noqa: PLR0913
        self,
        observations: dict[str, Any],
        parent_hypothesis: str = "",
        child_insights: str = "",
        pruned_directions: str = "",
        branching_factor: int = 3,
        master_hints: "dict[str, PersonaResult] | None" = None,
    ) -> list[dict[str, Any]]:
        """假设生成 — 基于观察结果生成改进假设。

        Args:
            master_hints: name -> PersonaResult 映射，由 HTR _ideate_node
                调用 4 个大师 strategy_generate mode 得到。None 或空 dict 时
                行为与原 ideate 完全一致（向后兼容）。
        """
        master_hints_context = self._format_master_hints(master_hints)
        if master_hints_context and self.logger:
            self.logger.info(
                f"[HTR-假设] 注入大师策略生成建议: {len(master_hints or {})} 位"
            )
        prompt_template = MarkdownPromptTemplate(
            "ideate_prompt.md",
            [
                "observations",
                "parent_hypothesis",
                "child_insights",
                "pruned_directions",
                "branching_factor",
                "master_hints_context",
            ],
            __file__,
        )
        prompt = prompt_template.format(
            observations=json.dumps(observations, ensure_ascii=False),
            parent_hypothesis=parent_hypothesis or "无",
            child_insights=child_insights or "无",
            pruned_directions=pruned_directions or "无",
            branching_factor=str(branching_factor),
            master_hints_context=master_hints_context or "无",
        )
        response = self.llm_service.invoke(prompt)
        # LLM 偶发返回空内容或非 JSON 时容错：返回空假设列表让 select 节点处理，
        # 上层会跳过该轮假设生成（不会崩溃）。
        result = parse_llm_json(response.content, default={"hypotheses": []})
        hypotheses = result.get("hypotheses", []) if isinstance(result, dict) else []
        if self.logger:
            self.logger.info(f"[HTR-假设] 生成 {len(hypotheses)} 个假设")
        return hypotheses

    def select(
        self,
        hypotheses: list[dict[str, Any]],
        max_select: int = 1,
    ) -> list[dict[str, Any]]:
        """选择阶段 — 从假设列表中选择最优的几个进行验证。

        Phase 2 串行模式：默认只选 1 个。Phase 5 并行模式可扩展。
        """
        if not hypotheses:
            return []
        # 简单策略：按方向多样性选择（避免全部选同一方向）
        selected: list[dict[str, Any]] = []
        seen_directions: set[str] = set()
        for h in hypotheses:
            direction = h.get("direction", "")
            if direction not in seen_directions or len(selected) < max_select:
                selected.append(h)
                seen_directions.add(direction)
            if len(selected) >= max_select:
                break
        if self.logger:
            self.logger.info(
                f"[HTR-选择] 从 {len(hypotheses)} 个假设中选 {len(selected)} 个"
            )
        return selected

    def backpropagate_insights(
        self,
        parent_hypothesis: str,
        child_results: list[dict[str, Any]],
        master_perspectives: "dict[str, PersonaResult] | None" = None,
    ) -> dict[str, Any]:
        """洞察反向传播 — 将子节点实验结果抽象为方向级教训。

        Args:
            master_perspectives: name -> PersonaResult 映射，由 HTR
                _backpropagate_node 调用 4 个大师 strategy_review mode 得到。
                None 或空 dict 时行为与原方法完全一致（向后兼容）。
        """
        master_context = self._format_master_perspectives(master_perspectives)
        if master_context and self.logger:
            self.logger.info(
                f"[HTR-反向传播] 注入大师反思视角: {len(master_perspectives or {})} 位"
            )
        prompt_template = MarkdownPromptTemplate(
            "backpropagate_prompt.md",
            ["parent_hypothesis", "child_results", "master_perspectives"],
            __file__,
        )
        prompt = prompt_template.format(
            parent_hypothesis=parent_hypothesis,
            child_results=json.dumps(child_results, ensure_ascii=False, default=str),
            master_perspectives=master_context or "无",
        )
        response = self.llm_service.invoke(prompt)
        # LLM 偶发返回空内容或非 JSON 时容错：返回默认洞察让 decide 节点继续。
        result = parse_llm_json(
            response.content, default={"insight": "LLM 响应解析失败，无洞察"}
        )
        if self.logger:
            self.logger.info(
                f"[HTR-反向传播] insight={result.get('insight', '')[:80] if isinstance(result, dict) else ''}"
            )
        return result if isinstance(result, dict) else {"insight": str(result)}

    def decide(
        self,
        tree_state: dict[str, Any],
    ) -> str:
        """决策阶段 — 基于树状态决定下一步行动（merge/continue/stop）。

        ADR-014 任务2：``tree_state["similar_experiences"]`` 由 HTR 的
        ``_decide_node`` 通过 Connector 经验图谱查询填入，非空时 LLM
        能参考历史相似策略的 sharpe 表现做合并/停止决策。
        """
        prompt_template = MarkdownPromptTemplate(
            "decide_prompt.md",
            [
                "node_count", "max_depth", "current_best_oos",
                "best_dev_score", "best_oos_score",
                "cycles_used", "max_cycles",
                "similar_experiences",
            ],
            __file__,
        )
        prompt = prompt_template.format(
            node_count=str(tree_state.get("node_count", 0)),
            max_depth=str(tree_state.get("max_depth", 0)),
            current_best_oos=str(tree_state.get("current_best_oos", "无")),
            best_dev_score=str(tree_state.get("best_dev_score", 0)),
            best_oos_score=str(tree_state.get("best_oos_score", "无")),
            cycles_used=str(tree_state.get("cycles_used", 0)),
            max_cycles=str(tree_state.get("max_cycles", 10)),
            similar_experiences=tree_state.get("similar_experiences", "无"),
        )
        response = self.llm_service.invoke(prompt)
        # LLM 偶发返回空内容或非 JSON 时容错：默认 continue 让循环继续，
        # 避免单次 LLM 失败直接终止整轮 HTR。
        result = parse_llm_json(response.content, default={"action": "continue"})
        action = result.get("action", "continue") if isinstance(result, dict) else "continue"
        if self.logger:
            self.logger.info(f"[HTR-决策] action={action}")
        return action
