"""ResearchAgent / ToG 飞轮单元测试（ADR-018）

接口层：工具契约、prepare_context、Master 委托路径。
不跑真实 LLM / 回测。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from long_earn.strategy_rd.research_agent import ResearchAgent


def _make_context() -> MagicMock:
    ctx = MagicMock()
    ctx.logger = MagicMock()
    ctx.monitoring = MagicMock()
    ctx.monitoring.track.return_value.__enter__ = MagicMock(return_value=None)
    ctx.monitoring.track.return_value.__exit__ = MagicMock(return_value=False)
    ctx.memory = MagicMock()
    ctx.memory.activate_events.return_value = ["事件A"]
    ctx.memory.save_experience.return_value = "sid_exp_1"
    ctx.memory.search.return_value = []
    ctx.connector = MagicMock()
    ctx.connector.graph.traverse.return_value = []
    ctx.operator_backlog = MagicMock()
    ctx.operator_backlog.submit.return_value = True
    ctx.backtest_service = MagicMock()
    ctx.backtest_service.run.return_value = {
        "metrics": {"sharpe_ratio": 0.5, "total_return": 0.1},
        "strategy_diagnostics": {"degenerate": False},
    }
    ctx.config.train_start_date = "2022-01-01"
    ctx.config.train_end_date = "2024-12-31"
    ctx.config.test_start_date = "2025-01-01"
    ctx.config.test_end_date = "2026-03-24"
    ctx.market_intelligence = None
    ctx.prepare_context = MagicMock(return_value="激活上下文")
    ctx.require_llm.return_value.get_llm.return_value = MagicMock()
    return ctx


class TestResearchAgentTools:
    """工具集契约"""

    @pytest.fixture
    def agent(self) -> ResearchAgent:
        with (
            patch(
                "long_earn.strategy_rd.research_agent.create_react_agent",
                return_value=MagicMock(),
            ),
            patch(
                "long_earn.strategy_rd.research_agent.MarkdownPromptTemplate",
            ),
        ):
            return ResearchAgent(_make_context())

    def test_tool_count(self, agent: ResearchAgent) -> None:
        tools = agent._build_tools()
        assert len(tools) >= 10

    def test_tool_names_include_tog_core(self, agent: ResearchAgent) -> None:
        names = {t.name for t in agent._build_tools()}
        assert "prepare_context" in names
        assert "expand_relations" in names
        assert "prune_paths" in names
        assert "run_backtest" in names
        assert "run_oos_gates" in names
        assert "record_path_outcome" in names
        assert "list_operators_tool" in names

    def test_expand_relations_registers_beam(
        self, agent: ResearchAgent
    ) -> None:
        tool = next(t for t in agent._build_tools() if t.name == "expand_relations")
        out = tool.invoke({"entity": "动量"})
        assert "path_" in out or "beam" in out
        assert len(agent._beam_paths) == 1

    def test_record_path_outcome_writes_memory(
        self, agent: ResearchAgent
    ) -> None:
        tool = next(
            t for t in agent._build_tools() if t.name == "record_path_outcome"
        )
        out = tool.invoke(
            {
                "path_summary": "momentum v1",
                "strategy_yaml": "name: x",
                "metrics_json": '{"sharpe_ratio": 1.0}',
            }
        )
        assert "sid_exp_1" in out
        agent.context.memory.save_experience.assert_called_once()

    def test_invoke_calls_prepare_context(self, agent: ResearchAgent) -> None:
        agent._agent.invoke.return_value = {
            "messages": [],
        }
        result = agent.invoke("研发动量策略")
        agent.context.prepare_context.assert_called()
        assert "summary" in result
        assert "beam_paths" in result


class TestPrepareContext:
    """RuntimeContext.prepare_context 基础设施"""

    def test_returns_activated_events_without_refresh(self) -> None:
        from long_earn.config import RuntimeContext

        ctx = MagicMock(spec=RuntimeContext)
        # 绑定真实方法
        ctx.memory = MagicMock()
        ctx.memory.activate_events.return_value = ["e1", "e2"]
        ctx.logger = MagicMock()
        ctx.market_intelligence = None
        text = RuntimeContext.prepare_context(ctx, "茅台")
        assert "e1" in text
        assert "e2" in text


class TestDefaultCollectorRegistry:
    def test_registers_kimi(self) -> None:
        from long_earn.event_inference.collectors import (
            create_default_collector_registry,
        )

        registry = create_default_collector_registry(market_intelligence=None)
        assert "kimi" in registry._collectors
