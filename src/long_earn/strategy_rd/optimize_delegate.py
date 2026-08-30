"""策略优化委托 — CLI optimize 与 ResearchAgent 工具链共用的薄实现。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from long_earn.strategy_rd.agents.mixins import KnowledgeContextMixin
from long_earn.strategy_rd.agents.strategy_develop_agent import _format_operator_catalog
from long_earn.strategy_rd.optimize_prompt import render_strategy_optimize_prompt

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext

_OPTIMIZE_CATEGORIES = [
    "三、财务分析类",
    "四、风险指标类",
    "五、量化策略类",
]


class OptimizeDelegate(KnowledgeContextMixin):
    """策略优化委托 — 实现 :class:`~long_earn.strategy_optimization.optimizer.StrategyResearchDelegate`。"""

    def __init__(self, context: RuntimeContext) -> None:
        self.context = context
        self.llm_service = context.require_llm()
        self.memory = context.require_memory()
        self.logger = context.logger
        self._knowledge_cache: dict[str, list[str]] = {}
        self._event_cache: dict[str, list[str]] = {}

    def _get_research_context(self, query: str) -> str:
        return KnowledgeContextMixin._get_knowledge_context(
            self, query, node_type="optimize", categories=_OPTIMIZE_CATEGORIES
        )

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
        """从记忆系统检索同类策略的历史经验，构造 prompt 段落。"""
        strategy_name = strategy.get("strategy_name", "") or ""
        factors = strategy.get("factors_used", []) or []
        factor_str = (
            " ".join(str(f) for f in factors) if isinstance(factors, list) else ""
        )

        candidate_queries = [
            "动量策略",
            "策略经验",
            strategy_name,
            factor_str,
            "策略优化",
        ]
        seen: set[str] = set()
        queries = [
            q for q in candidate_queries if q and q not in seen and not seen.add(q)
        ]

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
        lines = [f"- {p.name}: metrics={p.metrics}" for p in past]
        return "历史同类经验：\n" + "\n".join(lines)

    def optimize_strategy(
        self,
        strategy: dict[str, Any],
        improvement_suggestions: list,
        previous_backtest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """根据改进建议优化策略。"""
        suggestions_str = "\n".join([f"- {s}" for s in improvement_suggestions])
        knowledge_context = self._get_research_context("策略优化方法")
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
            operator_catalog=_format_operator_catalog(),
        )
        response = self.llm_service.invoke(prompt)

        optimized = strategy.copy()
        optimized["description"] = response.content
        optimized["optimized"] = True
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
