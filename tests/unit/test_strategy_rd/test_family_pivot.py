"""策略家族切换 + 算子目录注入 单元测试。

覆盖点：
1. ``_evaluate_branches`` 在 history_return<0 且 recent_return>0 时优先选"策略家族切换"
2. ``_format_operator_catalog`` 产出含已注册算子名的可读清单
3. ``run_loop`` 连续无改善轮数达阈值时改写 idea 换家族（mock 子图）
"""

from __future__ import annotations

from unittest.mock import MagicMock

from long_earn.config import RuntimeContext
from long_earn.services import MemoryService
from long_earn.services.backtest_service import BacktestService
from long_earn.services.llm_service import LLMService
from long_earn.services.logger_service import LoggerService
from long_earn.services.monitoring_service import MonitoringService
from long_earn.services.stock_service import StockService
from long_earn.strategy_rd.agents.strategy_research_agent import (
    OPTIMIZATION_DIRECTIONS,
    StrategyResearchAgent,
)


def _make_mock_context() -> RuntimeContext:
    """创建带 mock 服务的 RuntimeContext。"""
    mock_llm = MagicMock(spec=LLMService)
    mock_response = MagicMock()
    mock_response.content = '{"reflection": "test", "improvement_suggestions": []}'
    mock_llm.invoke.return_value = mock_response

    mock_memory = MagicMock(spec=MemoryService)
    mock_memory.search.return_value = []

    mock_config = MagicMock()
    mock_config.llm_type = "ollama"
    mock_config.llm_model = "test"
    mock_config.llm_base_url = "http://localhost"
    mock_config.max_iterations = 1
    mock_config.train_start_date = "2022-01-01"
    mock_config.test_end_date = "2026-01-05"
    mock_config.validation_start_date = "2026-01-06"
    mock_config.validation_end_date = "2026-07-10"
    mock_config.backtest_start_date = "2022-01-01"
    mock_config.backtest_end_date = "2026-01-05"

    return RuntimeContext(
        llm_service=mock_llm,
        memory=mock_memory,
        stock_service=MagicMock(spec=StockService),
        backtest_service=MagicMock(spec=BacktestService),
        logger=MagicMock(spec=LoggerService),
        monitoring=MagicMock(spec=MonitoringService),
        config=mock_config,
    )


class TestFamilyPivotBranch:
    """ToT 第 4 分支「策略家族切换」打分逻辑。"""

    def test_direction_exists(self):
        """OPTIMIZATION_DIRECTIONS 含「策略家族切换」分支。"""
        assert "策略家族切换" in OPTIMIZATION_DIRECTIONS
        config = OPTIMIZATION_DIRECTIONS["策略家族切换"]
        assert "family_long_term_return" in config["metrics"]

    def test_pivot_selected_when_history_loss_recent_gain(self):
        """history_return<0 + recent_return>0 → 家族切换得分最高。"""
        ctx = _make_mock_context()
        agent = StrategyResearchAgent(context=ctx)

        branches = [
            {"direction": "收益增强", "reflection": "", "improvement_suggestions": []},
            {"direction": "风险控制", "reflection": "", "improvement_suggestions": []},
            {
                "direction": "收益稳定性",
                "reflection": "",
                "improvement_suggestions": [],
            },
            {
                "direction": "策略家族切换",
                "reflection": "",
                "improvement_suggestions": [],
            },
        ]
        backtest_result = {
            "total_return": 0.15,
            "max_drawdown": -0.10,
            "sharpe_ratio": 0.6,
        }

        evaluated = agent._evaluate_branches(
            branches, backtest_result, history_return=-0.30
        )
        assert evaluated[0]["direction"] == "策略家族切换"
        assert evaluated[0]["score"] == 40

    def test_pivot_not_selected_when_history_positive(self):
        """history_return>0 → 家族切换不优先（得 5 分）。"""
        ctx = _make_mock_context()
        agent = StrategyResearchAgent(context=ctx)

        branches = [
            {"direction": "收益增强", "reflection": "", "improvement_suggestions": []},
            {
                "direction": "策略家族切换",
                "reflection": "",
                "improvement_suggestions": [],
            },
        ]
        backtest_result = {
            "total_return": 0.15,
            "max_drawdown": -0.10,
            "sharpe_ratio": 0.6,
        }

        evaluated = agent._evaluate_branches(
            branches, backtest_result, history_return=0.20
        )
        pivot = next(b for b in evaluated if b["direction"] == "策略家族切换")
        assert pivot["score"] == 5
        # 收益增强（return=0.15 < 0.10 阈值 → 15 分）应高于家族切换
        assert evaluated[0]["direction"] == "收益增强"

    def test_pivot_partial_when_history_loss_only(self):
        """history_return<0 但 recent_return<0 → 家族切换得 35 分（高于收益增强30）。"""
        ctx = _make_mock_context()
        agent = StrategyResearchAgent(context=ctx)

        branches = [
            {"direction": "收益增强", "reflection": "", "improvement_suggestions": []},
            {
                "direction": "策略家族切换",
                "reflection": "",
                "improvement_suggestions": [],
            },
        ]
        backtest_result = {
            "total_return": -0.05,
            "max_drawdown": -0.10,
            "sharpe_ratio": 0.3,
        }

        evaluated = agent._evaluate_branches(
            branches, backtest_result, history_return=-0.30
        )
        # 策略退化（recent<0）+ 历史亏损 → 家族切换 35 分 > 收益增强 30 分
        assert evaluated[0]["direction"] == "策略家族切换"
        pivot = evaluated[0]
        assert pivot["score"] == 35


class TestOperatorCatalogInjection:
    """算子目录清单注入 develop prompt。"""

    def test_format_operator_catalog_contains_known_ops(self):
        """_format_operator_catalog 输出含已注册算子名。"""
        from long_earn.strategy_rd.agents.strategy_develop_agent import (
            _format_operator_catalog,
        )

        catalog = _format_operator_catalog()
        # 核心算子必须在清单中
        assert "windowed" in catalog
        assert "shift" in catalog
        assert "filter_threshold" in catalog
        assert "rank_top" in catalog
        # windowed 的 agg 参数应可见
        assert "agg" in catalog

    def test_format_operator_catalog_is_string(self):
        """_format_operator_catalog 返回字符串。"""
        from long_earn.strategy_rd.agents.strategy_develop_agent import (
            _format_operator_catalog,
        )

        catalog = _format_operator_catalog()
        assert isinstance(catalog, str)
        assert len(catalog) > 0


class TestQDDiversitySelection:
    """ADR-015 B3: Quality-Diversity 行为描述符多样性选择。

    三级降级：strict（direction+family 双重不同）→ direction-only → full fallback。
    防止 HTR fan-out 选出全同家族假设，陷入局部最优。
    """

    def _make_agent(self):
        ctx = _make_mock_context()
        return StrategyResearchAgent(context=ctx)

    def test_strict_diversity_all_unique(self):
        """direction+family 全不同时，strict 轮即填满。"""
        agent = self._make_agent()
        hyps = [
            {"hypothesis": "A", "direction": "收益增强", "family": "momentum"},
            {"hypothesis": "B", "direction": "风险控制", "family": "mean_reversion"},
            {"hypothesis": "C", "direction": "收益稳定性", "family": "value"},
        ]
        selected = agent._select_with_diversity(hyps, max_select=3)
        assert len(selected) == 3
        assert {s["hypothesis"] for s in selected} == {"A", "B", "C"}

    def test_direction_only_fallback_when_family_repeats(self):
        """family 重复但 direction 不同 → strict 选首个，direction-only 补齐。"""
        agent = self._make_agent()
        # 前两个 family 相同（momentum），但 direction 不同
        hyps = [
            {"hypothesis": "A", "direction": "收益增强", "family": "momentum"},
            {"hypothesis": "B", "direction": "风险控制", "family": "momentum"},
            {"hypothesis": "C", "direction": "收益稳定性", "family": "value"},
        ]
        selected = agent._select_with_diversity(hyps, max_select=3)
        assert len(selected) == 3
        # A（strict）+ B（direction-only，family 重复但 direction 新）+ C（strict）
        assert {s["hypothesis"] for s in selected} == {"A", "B", "C"}

    def test_full_fallback_when_direction_repeats(self):
        """direction+family 都重复 → 降级为全选，不丢失 LLM 产出。"""
        agent = self._make_agent()
        hyps = [
            {"hypothesis": "A", "direction": "收益增强", "family": "momentum"},
            {"hypothesis": "B", "direction": "收益增强", "family": "momentum"},
        ]
        selected = agent._select_with_diversity(hyps, max_select=2)
        # strict 仅选 A（direction+family 都重复），direction-only 无新 direction，
        # fallback 降级全选 → B 也被纳入
        assert len(selected) == 2
        assert {s["hypothesis"] for s in selected} == {"A", "B"}

    def test_select_fewer_than_max_when_pool_small(self):
        """候选池小于 max_select 时只返回候选池大小。"""
        agent = self._make_agent()
        hyps = [
            {"hypothesis": "A", "direction": "收益增强", "family": "momentum"},
        ]
        selected = agent._select_with_diversity(hyps, max_select=3)
        assert len(selected) == 1
