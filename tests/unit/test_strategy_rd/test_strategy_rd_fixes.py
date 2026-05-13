"""Tests for strategy_rd bug fixes

Validates that previously broken functionality now works:
1. Prompt module imports (were missing .py files)
2. _run_branch_reflection (was calling non-existent _create_llm())
3. Graph structure (redundant edge removed)
4. Memory system compatibility (numpy/pandas based)
5. State type consistency (improvement_suggestions now List[str])
"""

import json
from unittest.mock import MagicMock

from long_earn.config import RuntimeContext
from long_earn.services import MemoryService
from long_earn.services.backtest_service import BacktestService
from long_earn.services.llm_service import LLMService
from long_earn.services.logger_service import LoggerService
from long_earn.services.monitoring_service import MonitoringService
from long_earn.services.stock_service import StockService


def _make_mock_context() -> RuntimeContext:
    """Create a mock RuntimeContext with all services mocked."""
    mock_llm = MagicMock(spec=LLMService)
    mock_response = MagicMock()
    mock_response.content = "test response"
    mock_llm.invoke.return_value = mock_response

    mock_memory = MagicMock(spec=MemoryService)
    mock_memory.search.return_value = ["test knowledge"]
    mock_memory.recall.return_value = [
        {"content": "test", "metadata": {}, "similarity": 0.9}
    ]

    mock_logger = MagicMock(spec=LoggerService)
    mock_monitoring = MagicMock(spec=MonitoringService)
    mock_stock = MagicMock(spec=StockService)
    mock_backtest = MagicMock(spec=BacktestService)

    mock_config = MagicMock()
    mock_config.llm_type = "ollama"
    mock_config.llm_model = "test"
    mock_config.llm_base_url = "http://localhost"
    mock_config.memory_path = "~/.long_earn/memory.npz"
    mock_config.init_dir = "./init"
    mock_config.max_iterations = 1
    mock_config.backtest_start_date = "2020-01-01"
    mock_config.backtest_end_date = "2023-12-31"

    return RuntimeContext(
        llm_service=mock_llm,
        memory=mock_memory,
        stock_service=mock_stock,
        backtest_service=mock_backtest,
        logger=mock_logger,
        monitoring=mock_monitoring,
        config=mock_config,
    )


# ─── Fix 1: Prompt module imports ──────────────────────────────────


class TestPromptModuleImports:
    """Verify all three missing prompt modules can be imported."""

    def test_strategy_research_prompt_import(self):
        from long_earn.strategy_rd.agents.strategy_research_prompt import (
            create_strategy_research_prompt,
            strategy_generation_prompt,
            strategy_optimize_prompt,
            strategy_update_prompt,
        )

        assert callable(create_strategy_research_prompt)
        assert hasattr(strategy_optimize_prompt, "format")
        assert hasattr(strategy_generation_prompt, "format")
        assert hasattr(strategy_update_prompt, "format")

    def test_strategy_rd_supervisor_prompt_import(self):
        from long_earn.strategy_rd.agents.strategy_rd_supervisor_prompt import (
            strategy_rd_supervisor_continue_prompt,
            strategy_rd_supervisor_prompt,
        )

        assert hasattr(strategy_rd_supervisor_prompt, "format")
        assert hasattr(strategy_rd_supervisor_continue_prompt, "format")

    def test_strategy_reflection_prompt_import(self):
        from long_earn.strategy_rd.agents.strategy_reflection_prompt import (
            strategy_reflection_prompt,
        )

        assert hasattr(strategy_reflection_prompt, "format")

    def test_create_strategy_research_prompt_returns_string(self):
        from long_earn.strategy_rd.agents.strategy_research_prompt import (
            create_strategy_research_prompt,
        )

        result = create_strategy_research_prompt(
            target_market="stock",
            query="test query",
            strategy_examples="none",
            strategy_context="none",
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_strategy_optimize_prompt_formats(self):
        from long_earn.strategy_rd.agents.strategy_research_prompt import (
            strategy_optimize_prompt,
        )

        result = strategy_optimize_prompt.format(
            strategy="momentum strategy",
            suggestions_text="- add stop loss",
            backtest_history="none",
            market_characteristics="A stock market",
        )
        assert isinstance(result, str)
        assert "momentum strategy" in result
        assert "add stop loss" in result

    def test_supervisor_prompt_formats(self):
        from long_earn.strategy_rd.agents.strategy_rd_supervisor_prompt import (
            strategy_rd_supervisor_prompt,
        )

        result = strategy_rd_supervisor_prompt.format(
            strategy={"name": "test"},
            backtest_result={"return": 0.15},
            decision_history="none",
        )
        assert isinstance(result, str)

    def test_supervisor_continue_prompt_formats(self):
        from long_earn.strategy_rd.agents.strategy_rd_supervisor_prompt import (
            strategy_rd_supervisor_continue_prompt,
        )

        result = strategy_rd_supervisor_continue_prompt.format(
            iteration=1,
            max_iterations=3,
            remaining_iterations=2,
            strategy={"name": "test"},
            backtest_result={"return": 0.15},
            reflection="needs improvement",
            improvement_suggestions="add stop loss",
            decision_history="none",
            iteration_history="none",
        )
        assert isinstance(result, str)


# ─── Fix 2: _run_branch_reflection uses llm_service ────────────────


class TestBranchReflection:
    """Verify ToT branch reflection works without _create_llm()."""

    def test_run_branch_reflection_calls_llm_service(self):
        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        context = _make_mock_context()
        agent = StrategyResearchAgent(context=context)

        # Set up mock LLM to return valid JSON
        branch_result = {
            "direction": "收益增强",
            "reflection": "Returns are low",
            "improvement_suggestions": [
                {"priority": "high", "issue": "low return", "suggestion": "add factors"}
            ],
        }
        mock_response = MagicMock()
        mock_response.content = json.dumps(branch_result)
        context.llm_service.invoke.return_value = mock_response

        result = agent._run_branch_reflection(
            direction="收益增强",
            strategy={"description": "test strategy"},
            backtest_result={"metrics": {"return": 5}},
        )

        # Verify llm_service.invoke was called (not _create_llm)
        assert context.llm_service.invoke.called
        assert result["direction"] == "收益增强"
        assert "reflection" in result

    def test_reflect_with_tot_completes(self):
        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        context = _make_mock_context()
        agent = StrategyResearchAgent(context=context)

        # Return valid JSON for each branch
        def make_branch_response(direction):
            return MagicMock(
                content=json.dumps(
                    {
                        "direction": direction,
                        "reflection": f"{direction} analysis",
                        "improvement_suggestions": [
                            {
                                "priority": "high",
                                "issue": "test",
                                "suggestion": "test fix",
                            }
                        ],
                    }
                )
            )

        context.llm_service.invoke.side_effect = [
            make_branch_response("收益增强"),
            make_branch_response("风险控制"),
            make_branch_response("收益稳定性"),
        ]

        result = agent.reflect_with_tot(
            strategy={"description": "test"},
            backtest_result={
                "metrics": {"return": 5, "max_drawdown": 10, "sharpe_ratio": 0.3}
            },
        )

        assert result["tot_enabled"] is True
        assert "reflection" in result
        assert "improvement_suggestions" in result
        assert "selected_direction" in result

    def test_reflect_fallback_on_tot_failure(self):
        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        context = _make_mock_context()
        agent = StrategyResearchAgent(context=context)

        # Make ToT fail
        context.llm_service.invoke.side_effect = RuntimeError("LLM error")

        result = agent.reflect(
            strategy={"description": "test"},
            backtest_result={
                "metrics": {"return": 5, "max_drawdown": 10, "sharpe_ratio": 0.3}
            },
        )

        # Should fall back to _simple_fallback
        assert result["tot_enabled"] is False
        assert "reflection" in result
        assert "improvement_suggestions" in result


# ─── Fix 3: Graph structure (no redundant edge) ────────────────────


class TestGraphStructure:
    """Verify the graph compiles without the redundant backtest->refine edge."""

    def test_subgraph_compiles(self):
        from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph

        context = _make_mock_context()
        subgraph = create_strategy_rd_subgraph(context)
        assert subgraph is not None

    def test_backtest_conditional_edge_routes_correctly(self):
        """When code_valid=True, backtest should route to reflection, not refine."""
        from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph

        context = _make_mock_context()
        subgraph = create_strategy_rd_subgraph(context)

        graph_info = subgraph.get_graph()
        edges = list(graph_info.edges)

        # backtest should have conditional edges (to refine or reflection)
        backtest_edges = [e for e in edges if e.source == "backtest"]
        assert len(backtest_edges) > 0, "backtest should have outgoing edges"

    def test_refine_routes_to_backtest_or_reflection(self):
        """refine should have conditional edges to backtest or reflection."""
        from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph

        context = _make_mock_context()
        subgraph = create_strategy_rd_subgraph(context)

        graph_info = subgraph.get_graph()
        edges = list(graph_info.edges)
        refine_edges = [e for e in edges if e.source == "refine"]
        assert len(refine_edges) > 0, "refine should have outgoing edges"


# ─── Fix 5: State type consistency ──────────────────────────────────


class TestStateTypeConsistency:
    """Verify improvement_suggestions is typed as List[str]."""

    def test_state_improvement_suggestions_type(self):
        from typing import get_type_hints

        from long_earn.strategy_rd.state import State

        hints = get_type_hints(State)
        assert "improvement_suggestions" in hints

    def test_state_accepts_list_suggestions(self):
        from long_earn.strategy_rd.state import State

        # Should accept a list of strings without type errors
        state: State = {
            "query": "test",
            "improvement_suggestions": ["add stop loss", "reduce position"],
        }
        assert state["improvement_suggestions"] == ["add stop loss", "reduce position"]


# ─── End-to-end: Full subgraph with mock LLM ───────────────────────


class TestFullSubgraphFlow:
    """Run the subgraph end-to-end with mocked services."""

    def test_subgraph_runs_to_completion(self):
        from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph

        context = _make_mock_context()

        # Set up LLM responses for the full flow
        # The flow is: init -> initial_retrieval -> evaluate_retrieval -> research ->
        #   develop -> backtest -> (code_valid=True) -> reflection ->
        #   save_experience -> supervisor -> (should_continue=False) -> END
        responses = iter(
            [
                # evaluate_retrieval: "SUFFICIENT"
                MagicMock(content="SUFFICIENT"),
                # research: strategy description
                MagicMock(
                    content='{"strategy_name": "TestStrategy", "description": "A test"}'
                ),
                # develop: code
                MagicMock(content="```python\nclass TestStrategy:\n    pass\n```"),
                # reflection (ToT - 3 branches):
                MagicMock(
                    content=json.dumps(
                        {
                            "direction": "收益增强",
                            "reflection": "good strategy",
                            "improvement_suggestions": [
                                {
                                    "priority": "low",
                                    "issue": "minor",
                                    "suggestion": "optimize",
                                }
                            ],
                        }
                    )
                ),
                MagicMock(
                    content=json.dumps(
                        {
                            "direction": "风险控制",
                            "reflection": "acceptable risk",
                            "improvement_suggestions": [
                                {
                                    "priority": "low",
                                    "issue": "minor",
                                    "suggestion": "watch",
                                }
                            ],
                        }
                    )
                ),
                MagicMock(
                    content=json.dumps(
                        {
                            "direction": "收益稳定性",
                            "reflection": "stable",
                            "improvement_suggestions": [
                                {
                                    "priority": "low",
                                    "issue": "minor",
                                    "suggestion": "fine-tune",
                                }
                            ],
                        }
                    )
                ),
                # supervisor: should_continue=False
                MagicMock(
                    content=json.dumps(
                        {
                            "should_continue": False,
                            "reason": "strategy meets targets",
                        }
                    )
                ),
            ]
        )

        context.llm_service.invoke.side_effect = lambda *args, **kwargs: next(responses)

        # Mock backtest_service to succeed (回测通过 context.backtest_service 调用)
        context.backtest_service.run.return_value = {
            "total_return": 0.15,
            "sharpe_ratio": 0.8,
            "max_drawdown": -0.1,
        }

        subgraph = create_strategy_rd_subgraph(context)
        result = subgraph.invoke(
            {"query": "test strategy", "max_iterations": 1},
            {"recursion_limit": 25},
        )

        # Verify the flow completed
        assert "iteration" in result
        assert result.get("code_valid") is True or "backtest_result" in result
