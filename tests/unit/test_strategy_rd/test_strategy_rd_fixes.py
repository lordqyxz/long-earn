"""策略研发接口测试 — Memory / Develop / OptimizeDelegate。"""

from unittest.mock import MagicMock

from long_earn.config import RuntimeContext
from long_earn.services import MemoryService, StrategyExperience
from long_earn.services.backtest_service import BacktestService
from long_earn.services.llm_service import LLMService
from long_earn.services.logger_service import LoggerService
from long_earn.services.monitoring_service import MonitoringService
from long_earn.services.stock_service import StockService
from long_earn.strategy_rd.optimize_delegate import OptimizeDelegate


def _make_mock_context() -> RuntimeContext:
    """创建带 mock 服务的 RuntimeContext"""
    mock_llm = MagicMock(spec=LLMService)
    mock_response = MagicMock()
    mock_response.content = "test response"
    mock_llm.invoke.return_value = mock_response

    mock_memory = MagicMock(spec=MemoryService)
    mock_memory.search.return_value = ["test knowledge"]
    mock_memory.save_experience.return_value = "test-exp-id"

    mock_config = MagicMock()
    mock_config.llm_type = "ollama"
    mock_config.llm_model = "test"
    mock_config.llm_base_url = "http://localhost"
    mock_config.init_dir = "./init"
    mock_config.max_iterations = 1
    mock_config.backtest_start_date = "2020-01-01"
    mock_config.backtest_end_date = "2023-12-31"

    return RuntimeContext(
        llm_service=mock_llm,
        memory=mock_memory,
        stock_service=MagicMock(spec=StockService),
        backtest_service=MagicMock(spec=BacktestService),
        logger=MagicMock(spec=LoggerService),
        monitoring=MagicMock(spec=MonitoringService),
        config=mock_config,
    )


class TestOptimizePromptModule:
    def test_render_strategy_optimize_prompt_returns_string(self):
        from long_earn.strategy_rd.optimize_prompt import (
            render_strategy_optimize_prompt,
        )

        result = render_strategy_optimize_prompt(
            strategy={"strategy_name": "S"},
            suggestions_text="- 提升 sharpe",
            backtest_history="无",
            market_characteristics="无",
        )
        assert isinstance(result, str)
        assert len(result) > 0


class TestEvolutionLineage:
    """OptimizeDelegate 多轮演进与记忆系统交互。"""

    def test_optimize_uses_previous_optimized_as_base(self):
        context = _make_mock_context()
        delegate = OptimizeDelegate(context=context)
        context.memory.search_experience = MagicMock(
            return_value=[
                StrategyExperience(
                    name="OldStrategy",
                    code="...",
                    rationale="...",
                    metrics={"sharpe_ratio": 1.5, "total_return": 0.3},
                )
            ]
        )
        mock_response = MagicMock()
        mock_response.content = "optimized desc"
        context.llm_service.invoke.return_value = mock_response

        previous_backtest = {
            "total_return": 0.123,
            "sharpe_ratio": 0.45,
            "max_drawdown": -0.18,
            "trading_days": 250,
        }
        result = delegate.optimize_strategy(
            strategy={"strategy_name": "TestS", "factors_used": ["roe"]},
            improvement_suggestions=["降低回撤"],
            previous_backtest=previous_backtest,
        )

        called_prompt = context.llm_service.invoke.call_args[0][0]
        assert "0.123" in called_prompt
        assert "OldStrategy" in called_prompt
        assert result.get("optimized") is True
        assert isinstance(result.get("evolution_lineage"), list)
        assert len(result["evolution_lineage"]) == 1
        assert result["evolution_lineage"][0]["had_backtest"] is True

    def test_optimize_marks_unreliable_metrics(self):
        context = _make_mock_context()
        context.memory.search_experience = MagicMock(return_value=[])
        delegate = OptimizeDelegate(context=context)
        mock_response = MagicMock()
        mock_response.content = "optimized desc"
        context.llm_service.invoke.return_value = mock_response

        delegate.optimize_strategy(
            strategy={"strategy_name": "TestS"},
            improvement_suggestions=["a"],
            previous_backtest={
                "total_return": 0,
                "sharpe_ratio": 0,
                "metrics_unreliable": True,
                "error": "数据不足",
            },
        )

        called_prompt = context.llm_service.invoke.call_args[0][0]
        assert "数据不足" in called_prompt or "占位" in called_prompt


class TestSearchExperienceMinSharpeBoundary:
    """search_experience min_sharpe 过滤的边界正确性。"""

    def _make_service(self):
        from long_earn.services.memory_service import MemoryServiceImpl

        config = MagicMock()
        service = MemoryServiceImpl(config, MagicMock())
        return service

    def test_zero_sharpe_strategy_excluded_when_min_sharpe_is_zero(self):
        svc = self._make_service()

        def fake_store_search(query, k=10, **kw):
            return [
                {
                    "content": "A",
                    "metadata": {
                        "experience_type": "strategy",
                        "term": "Bad",
                        "backtest_metrics": {"sharpe_ratio": -0.1},
                    },
                    "similarity": 0.9,
                },
                {
                    "content": "B",
                    "metadata": {
                        "experience_type": "strategy",
                        "term": "Zero",
                        "backtest_metrics": {"sharpe_ratio": 0.0},
                    },
                    "similarity": 0.8,
                },
                {
                    "content": "C",
                    "metadata": {
                        "experience_type": "strategy",
                        "term": "Good",
                        "backtest_metrics": {"sharpe_ratio": 1.5},
                    },
                    "similarity": 0.7,
                },
            ]

        svc._store.search = fake_store_search  # type: ignore[method-assign]

        result = svc.search_experience(query="x", k=5, min_sharpe=0.0)

        names = {r.name for r in result}
        assert "Bad" not in names
        assert "Zero" in names
        assert "Good" in names

    def test_missing_sharpe_filtered_when_min_sharpe_set(self):
        svc = self._make_service()

        def fake_store_search(query, k=10, **kw):
            return [
                {
                    "content": ".",
                    "metadata": {
                        "experience_type": "strategy",
                        "term": "NoMetric",
                        "backtest_metrics": {},
                    },
                    "similarity": 0.9,
                },
            ]

        svc._store.search = fake_store_search  # type: ignore[method-assign]

        result = svc.search_experience(query="x", k=5, min_sharpe=0.5)
        assert result == []

    def test_min_sharpe_none_no_filter(self):
        svc = self._make_service()

        def fake_store_search(query, k=10, **kw):
            return [
                {
                    "content": ".",
                    "metadata": {
                        "experience_type": "strategy",
                        "term": "Anything",
                        "backtest_metrics": {"sharpe_ratio": -2.0},
                    },
                    "similarity": 0.9,
                },
            ]

        svc._store.search = fake_store_search  # type: ignore[method-assign]

        result = svc.search_experience(query="x", k=5, min_sharpe=None)
        assert len(result) == 1
        assert result[0].name == "Anything"


class TestMemorySaveExperience:
    """记忆系统保存经验时必须把回测指标存进 backtest_metrics 元数据"""

    def test_flat_backtest_keys_are_persisted(self):
        from long_earn.services.memory_service import MemoryServiceImpl

        config = MagicMock()
        service = MemoryServiceImpl(config, MagicMock(spec=LoggerService))

        exp_id = service.save_experience(
            StrategyExperience(
                name="X",
                code="yaml",
                rationale="r",
                metrics={
                    "total_return": 0.42,
                    "sharpe_ratio": 1.2,
                    "max_drawdown": -0.1,
                    "outcome": "success",
                },
                reflection="ok",
            )
        )

        assert exp_id
        substances = service._store.get_all()
        assert len(substances) == 1
        meta = substances[0].metadata
        metrics = meta.get("backtest_metrics", {})
        assert metrics.get("total_return") == 0.42
        assert metrics.get("sharpe_ratio") == 1.2
        assert metrics.get("max_drawdown") == -0.1
        assert meta.get("outcome") == "success"
        assert meta.get("backtest_success") is True

    def test_candidate_outcome_not_marked_success(self):
        from long_earn.services.memory_service import MemoryServiceImpl

        config = MagicMock()
        service = MemoryServiceImpl(config, MagicMock(spec=LoggerService))

        service.save_experience(
            StrategyExperience(
                name="Candidate",
                code="yaml",
                rationale="r",
                metrics={
                    "sharpe_ratio": 1.5,
                    "outcome": "candidate",
                },
            )
        )

        meta = service._store.get_all()[0].metadata
        assert meta.get("outcome") == "candidate"
        assert meta.get("backtest_success") is False

    def test_missing_outcome_not_marked_success(self):
        from long_earn.services.memory_service import MemoryServiceImpl

        config = MagicMock()
        service = MemoryServiceImpl(config, MagicMock(spec=LoggerService))

        service.save_experience(
            StrategyExperience(
                name="Legacy",
                code="yaml",
                rationale="r",
                metrics={"sharpe_ratio": 1.0},
            )
        )

        meta = service._store.get_all()[0].metadata
        assert meta.get("outcome") is None
        assert meta.get("backtest_success") is False


class TestDevelopAgentRequiredOutcome:
    """develop agent 成功案例检索须 required_outcome=success。"""

    def test_get_experience_context_passes_required_outcome(self):
        from long_earn.strategy_rd.agents.strategy_develop_agent import (
            StrategyDevelopAgent,
        )

        context = _make_mock_context()
        agent = StrategyDevelopAgent(context=context)
        context.memory.search_experience = MagicMock(
            return_value=[
                StrategyExperience(
                    name="Winner",
                    code="name: w\n",
                    rationale="ok",
                    metrics={"sharpe_ratio": 1.2, "outcome": "success"},
                )
            ]
        )

        result = agent._get_experience_context("momentum strategy")

        context.memory.search_experience.assert_called_once_with(
            query="momentum strategy",
            k=2,
            min_sharpe=0.5,
            required_outcome="success",
        )
        assert "Winner" in result
        assert "成功案例" in result


class TestSearchExperienceRequiredOutcome:
    """search_experience required_outcome 过滤 candidate 污染飞轮读路径。"""

    def _make_service(self):
        from long_earn.services.memory_service import MemoryServiceImpl

        config = MagicMock()
        service = MemoryServiceImpl(config, MagicMock())
        return service

    def test_required_outcome_success_filters_candidate(self):
        svc = self._make_service()

        def fake_store_search(query, k=10, **kw):
            return [
                {
                    "content": "A",
                    "metadata": {
                        "experience_type": "strategy",
                        "term": "Candidate",
                        "backtest_metrics": {
                            "sharpe_ratio": 1.5,
                            "outcome": "candidate",
                        },
                    },
                    "similarity": 0.9,
                },
                {
                    "content": "B",
                    "metadata": {
                        "experience_type": "strategy",
                        "term": "Winner",
                        "backtest_metrics": {
                            "sharpe_ratio": 1.2,
                            "outcome": "success",
                        },
                    },
                    "similarity": 0.8,
                },
            ]

        svc._store.search = fake_store_search  # type: ignore[method-assign]

        result = svc.search_experience(
            query="x", k=5, min_sharpe=0.5, required_outcome="success"
        )

        names = {r.name for r in result}
        assert "Candidate" not in names
        assert "Winner" in names

    def test_required_outcome_reads_top_level_meta(self):
        svc = self._make_service()

        def fake_store_search(query, k=10, **kw):
            return [
                {
                    "content": ".",
                    "metadata": {
                        "experience_type": "strategy",
                        "term": "TopLevel",
                        "outcome": "SUCCESS",
                        "backtest_metrics": {"sharpe_ratio": 0.8},
                    },
                    "similarity": 0.9,
                },
            ]

        svc._store.search = fake_store_search  # type: ignore[method-assign]

        result = svc.search_experience(query="x", k=5, required_outcome="success")
        assert len(result) == 1
        assert result[0].name == "TopLevel"
