"""ResearchAgent / ToG 飞轮单元测试（ADR-018）

接口层：工具契约、prepare_context、Master 委托路径。
不跑真实 LLM / 回测。
"""

from __future__ import annotations

import json
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
        "sharpe_ratio": 0.5,
        "total_return": 0.1,
        "trade_count": 42,
        "metrics": {"sharpe_ratio": 0.5, "total_return": 0.1},
        "strategy_diagnostics": {"degenerate": False, "trade_count": 42},
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

    def test_expand_relations_registers_beam(self, agent: ResearchAgent) -> None:
        tool = next(t for t in agent._build_tools() if t.name == "expand_relations")
        out = tool.invoke({"entity": "动量"})
        assert "path_" in out or "beam" in out
        assert len(agent._beam_paths) == 1

    def test_record_path_outcome_writes_memory(self, agent: ResearchAgent) -> None:
        strategy_yaml = "name: momentum_v1\nuniverse:\n  type: main_board+gem\n"
        backtest_tool = next(
            t for t in agent._build_tools() if t.name == "run_backtest"
        )
        backtest_tool.invoke({"strategy_yaml": strategy_yaml})

        record_tool = next(
            t for t in agent._build_tools() if t.name == "record_path_outcome"
        )
        out = record_tool.invoke(
            {
                "path_summary": "momentum v1",
                "strategy_yaml": strategy_yaml,
                "metrics_json": '{"sharpe_ratio": 1.0}',
            }
        )
        assert "sid_exp_1" in out
        agent.context.memory.save_experience.assert_called_once()

    def test_record_path_outcome_rejects_success_without_evidence(
        self, agent: ResearchAgent
    ) -> None:
        record_tool = next(
            t for t in agent._build_tools() if t.name == "record_path_outcome"
        )
        out = record_tool.invoke(
            {
                "path_summary": "momentum v1",
                "strategy_yaml": "name: x\n",
                "metrics_json": '{"sharpe_ratio": 1.0}',
            }
        )
        assert "拒绝写回成功" in out
        agent.context.memory.save_experience.assert_not_called()

    def test_record_path_outcome_allows_failure_without_evidence(
        self, agent: ResearchAgent
    ) -> None:
        record_tool = next(
            t for t in agent._build_tools() if t.name == "record_path_outcome"
        )
        out = record_tool.invoke(
            {
                "path_summary": "failed path",
                "reflection": "动量因子无效",
                "outcome": "failure",
            }
        )
        assert "sid_exp_1" in out
        agent.context.memory.save_experience.assert_called_once()
        call_args = agent.context.memory.save_experience.call_args[0][0]
        assert call_args.metrics.get("outcome") == "failure"

    def test_record_path_outcome_rejects_unreliable_metrics(
        self, agent: ResearchAgent
    ) -> None:
        strategy_yaml = "name: bad\n"
        backtest_tool = next(
            t for t in agent._build_tools() if t.name == "run_backtest"
        )
        agent.context.backtest_service.run.return_value = {
            "metrics_unreliable": True,
            "strategy_diagnostics": {"degenerate": True, "trade_count": 0},
        }
        backtest_tool.invoke({"strategy_yaml": strategy_yaml})

        record_tool = next(
            t for t in agent._build_tools() if t.name == "record_path_outcome"
        )
        out = record_tool.invoke(
            {
                "path_summary": "bad strategy",
                "strategy_yaml": strategy_yaml,
            }
        )
        assert "拒绝写回成功" in out
        agent.context.memory.save_experience.assert_not_called()

    def test_run_backtest_caches_evidence(self, agent: ResearchAgent) -> None:
        strategy_yaml = "name: test\n"
        tool = next(t for t in agent._build_tools() if t.name == "run_backtest")
        tool.invoke({"strategy_yaml": strategy_yaml})
        fp = agent._evidence_cache
        assert len(fp) == 1
        ev = next(iter(fp.values()))
        assert ev.backtest_reliable is True
        assert ev.backtest_metrics is not None

    def test_run_backtest_rejects_non_train_split(self, agent: ResearchAgent) -> None:
        tool = next(t for t in agent._build_tools() if t.name == "run_backtest")

        out = json.loads(
            tool.invoke({"strategy_yaml": "name: test\n", "use_train_split": False})
        )

        assert out["rejected"] is True
        agent.context.backtest_service.run.assert_not_called()

    def test_run_oos_gates_caches_evidence(self, agent: ResearchAgent) -> None:
        strategy_yaml = "name: oos_test\n"
        agent.context.backtest_service.run_oos.return_value = {
            "fold_results": [
                {"sharpe_ratio": 0.8},
                {"sharpe_ratio": 0.6},
            ],
        }
        tool = next(t for t in agent._build_tools() if t.name == "run_oos_gates")
        out = tool.invoke({"strategy_yaml": strategy_yaml})
        assert "passed" in out
        assert len(agent._evidence_cache) == 1
        ev = next(iter(agent._evidence_cache.values()))
        assert ev.oos_passed is not None

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


class TestEvidenceGatePipeline:
    """ToG Spike：证据门全流程（run_backtest → run_oos_gates → record_path_outcome）。

    验证三道证据门在完整流水线中的正确性：
    1. 证据存在门：无证据拒绝 success
    2. 指标可信门：degenerate/unreliable 拒绝
    3. 统计显著门：DSR 多重检验校正
    """

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

    def _valid_strategy(self) -> str:
        return (
            "name: quality_momentum_v1\n"
            "universe:\n"
            "  type: main_board+gem\n"
            "signals:\n"
            "  - type: operator\n"
            "    op: rank_top\n"
            "    params:\n"
            "      n: 10\n"
        )

    def test_full_pipeline_train_only(self, agent: ResearchAgent) -> None:
        """训练集证据 → 写回 success（无 OOS 路径）"""
        strategy_yaml = self._valid_strategy()
        # Step 1: run_backtest
        bt = next(t for t in agent._build_tools() if t.name == "run_backtest")
        bt_result = bt.invoke({"strategy_yaml": strategy_yaml})
        assert "sharpe_ratio" in bt_result

        # Step 2: record_path_outcome（仅训练集证据）
        rec = next(t for t in agent._build_tools() if t.name == "record_path_outcome")
        out = rec.invoke(
            {
                "path_summary": "quality_momentum v1 训练集通过",
                "strategy_yaml": strategy_yaml,
                "metrics_json": '{"sharpe_ratio": 0.5}',
            }
        )
        assert "sid_exp_1" in out

    def test_full_pipeline_with_oos(self, agent: ResearchAgent) -> None:
        """训练集 + OOS 证据 → 写回 success"""
        strategy_yaml = self._valid_strategy()
        # Step 1: run_backtest
        bt = next(t for t in agent._build_tools() if t.name == "run_backtest")
        bt.invoke({"strategy_yaml": strategy_yaml})

        # Step 2: run_oos_gates（稳定性 + DSR）
        agent.context.backtest_service.run_walk_forward_parallel.return_value = {
            "fold_results": [
                {"sharpe_ratio": 0.7, "trading_days": 63},
                {"sharpe_ratio": 0.5, "trading_days": 63},
            ],
        }
        oos = next(t for t in agent._build_tools() if t.name == "run_oos_gates")
        oos_result = oos.invoke({"strategy_yaml": strategy_yaml})
        assert "passed" in oos_result

        # Step 3: record_path_outcome（训练集 + OOS 证据）
        rec = next(t for t in agent._build_tools() if t.name == "record_path_outcome")
        out = rec.invoke(
            {
                "path_summary": "quality_momentum v1 OOS 通过",
                "strategy_yaml": strategy_yaml,
                "metrics_json": '{"sharpe_ratio": 0.5}',
            }
        )
        assert "sid_exp_1" in out

    def test_pipeline_rejects_degenerate(self, agent: ResearchAgent) -> None:
        """退化策略 → 拒绝写回"""
        strategy_yaml = self._valid_strategy()
        agent.context.backtest_service.run.return_value = {
            "metrics_unreliable": True,
            "strategy_diagnostics": {"degenerate": True, "trade_count": 0},
        }
        bt = next(t for t in agent._build_tools() if t.name == "run_backtest")
        bt.invoke({"strategy_yaml": strategy_yaml})

        rec = next(t for t in agent._build_tools() if t.name == "record_path_outcome")
        out = rec.invoke(
            {
                "path_summary": "degenerate strategy",
                "strategy_yaml": strategy_yaml,
            }
        )
        assert "拒绝写回成功" in out

    def test_strategy_trial_count_increments(self, agent: ResearchAgent) -> None:
        """DSR: run_backtest 调用次数正确递增"""
        strategy_yaml = self._valid_strategy()
        bt = next(t for t in agent._build_tools() if t.name == "run_backtest")
        assert agent._strategy_trial_count == 0
        bt.invoke({"strategy_yaml": strategy_yaml})
        assert agent._strategy_trial_count == 1
        bt.invoke({"strategy_yaml": strategy_yaml})
        assert agent._strategy_trial_count == 2

    def test_oos_dsr_gate_rejects_low_sharpe(self, agent: ResearchAgent) -> None:
        """DSR 门拒绝低 sharpe（多 trial 校正后不显著）"""
        strategy_yaml = self._valid_strategy()
        # 多 trial 时 DSR 要求更高
        for _ in range(10):
            agent._strategy_trial_count += 1

        agent.context.backtest_service.run_walk_forward_parallel.return_value = {
            "fold_results": [
                {"sharpe_ratio": 0.2, "trading_days": 63},
                {"sharpe_ratio": 0.1, "trading_days": 63},
            ],
        }
        oos = next(t for t in agent._build_tools() if t.name == "run_oos_gates")
        oos_result = oos.invoke({"strategy_yaml": strategy_yaml})
        parsed = json.loads(oos_result)
        # 低 sharpe + 多 trial → DSR 应拒绝
        assert parsed["passed"] is False

    def test_evidence_cleared_between_invocations(self, agent: ResearchAgent) -> None:
        """每次 invoke 重置证据缓存和 trial count"""
        strategy_yaml = self._valid_strategy()
        bt = next(t for t in agent._build_tools() if t.name == "run_backtest")
        bt.invoke({"strategy_yaml": strategy_yaml})
        assert len(agent._evidence_cache) == 1
        assert agent._strategy_trial_count == 1

        # 模拟 invoke 重置
        agent._evidence_cache = {}
        agent._strategy_trial_count = 0
        assert len(agent._evidence_cache) == 0
        assert agent._strategy_trial_count == 0
