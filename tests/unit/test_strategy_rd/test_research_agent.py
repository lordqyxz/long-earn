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
                "long_earn.strategy_rd.research_agent.create_event_inference_subgraph",
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

        agent.context.backtest_service.run_oos.return_value = {
            "fold_results": [
                {"test": {"sharpe_ratio": 0.7}, "trading_days": 63},
                {"test": {"sharpe_ratio": 0.5}, "trading_days": 63},
            ],
        }
        oos_tool = next(t for t in agent._build_tools() if t.name == "run_oos_gates")
        oos_result = json.loads(oos_tool.invoke({"strategy_yaml": strategy_yaml}))
        assert oos_result["passed"] is True

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
        call_args = agent.context.memory.save_experience.call_args[0][0]
        assert call_args.metrics.get("outcome") == "success"

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
                {"test": {"sharpe_ratio": 0.8}},
                {"test": {"sharpe_ratio": 0.6}},
            ],
        }
        tool = next(t for t in agent._build_tools() if t.name == "run_oos_gates")
        out = tool.invoke({"strategy_yaml": strategy_yaml})
        parsed = json.loads(out)
        assert "passed" in parsed
        assert "stability" in parsed
        assert "merge" in parsed
        assert "dsr" in parsed
        assert "pbo" in parsed
        assert "mintrl" in parsed["dsr"]
        assert "haircut" in parsed["dsr"]
        assert parsed["dsr"]["observed_sharpe_source"] == "oos_mean"
        assert len(agent._evidence_cache) == 1
        ev = next(iter(agent._evidence_cache.values()))
        assert ev.oos_passed is not None
        agent.context.backtest_service.run.assert_called()
        agent.context.backtest_service.run_oos.assert_called()

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
                "long_earn.strategy_rd.research_agent.create_event_inference_subgraph",
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
        """训练集证据 → 拒绝写回 success（须 OOS 硬门）"""
        strategy_yaml = self._valid_strategy()
        # Step 1: run_backtest
        bt = next(t for t in agent._build_tools() if t.name == "run_backtest")
        bt_result = bt.invoke({"strategy_yaml": strategy_yaml})
        assert "sharpe_ratio" in bt_result

        # Step 2: record_path_outcome（仅训练集证据 → 拒绝 success）
        rec = next(t for t in agent._build_tools() if t.name == "record_path_outcome")
        out = rec.invoke(
            {
                "path_summary": "quality_momentum v1 训练集通过",
                "strategy_yaml": strategy_yaml,
                "metrics_json": '{"sharpe_ratio": 0.5}',
            }
        )
        assert "拒绝写回成功" in out
        assert "须 OOS 硬性门控通过" in out
        agent.context.memory.save_experience.assert_not_called()

    def test_full_pipeline_train_only_as_candidate(self, agent: ResearchAgent) -> None:
        """训练集证据 → candidate 可写回"""
        strategy_yaml = self._valid_strategy()
        bt = next(t for t in agent._build_tools() if t.name == "run_backtest")
        bt.invoke({"strategy_yaml": strategy_yaml})

        rec = next(t for t in agent._build_tools() if t.name == "record_path_outcome")
        out = rec.invoke(
            {
                "path_summary": "quality_momentum v1 训练集候选",
                "strategy_yaml": strategy_yaml,
                "metrics_json": '{"sharpe_ratio": 0.5}',
                "outcome": "candidate",
            }
        )
        assert "sid_exp_1" in out
        agent.context.memory.save_experience.assert_called_once()
        call_args = agent.context.memory.save_experience.call_args[0][0]
        assert call_args.metrics.get("outcome") == "candidate"

    def test_full_pipeline_with_oos(self, agent: ResearchAgent) -> None:
        """训练集 + OOS 证据 → 写回 success"""
        strategy_yaml = self._valid_strategy()
        # Step 1: run_backtest
        bt = next(t for t in agent._build_tools() if t.name == "run_backtest")
        bt.invoke({"strategy_yaml": strategy_yaml})

        # Step 2: run_oos_gates（稳定性 + 合并 + DSR/PBO 诊断）
        agent.context.backtest_service.run_oos.return_value = {
            "fold_results": [
                {"test": {"sharpe_ratio": 0.7}, "trading_days": 63},
                {"test": {"sharpe_ratio": 0.5}, "trading_days": 63},
            ],
        }
        oos = next(t for t in agent._build_tools() if t.name == "run_oos_gates")
        oos_result = oos.invoke({"strategy_yaml": strategy_yaml})
        parsed_oos = json.loads(oos_result)
        assert "passed" in parsed_oos
        assert parsed_oos["passed"] is True
        assert parsed_oos["stability"]["passed"] is True

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

    def test_oos_dsr_diagnostic_fails_without_blocking_hard_gate(
        self, agent: ResearchAgent
    ) -> None:
        """DSR 诊断失败不翻转硬性门 passed（稳定性/合并仍可通过）。"""
        strategy_yaml = self._valid_strategy()
        for i in range(10):
            agent._trial_fingerprints.add(f"fp_{i}")

        agent.context.backtest_service.run_oos.return_value = {
            "fold_results": [
                {"test": {"sharpe_ratio": 0.2}, "trading_days": 63},
                {"test": {"sharpe_ratio": 0.1}, "trading_days": 63},
            ],
        }
        oos = next(t for t in agent._build_tools() if t.name == "run_oos_gates")
        parsed = json.loads(oos.invoke({"strategy_yaml": strategy_yaml}))
        assert parsed["stability"]["passed"] is True
        assert parsed["merge"]["passed"] is True
        assert parsed["passed"] is True
        assert parsed["dsr"]["status"] == "failed"
        assert parsed["dsr"]["passed"] is False

        rec = next(t for t in agent._build_tools() if t.name == "record_path_outcome")
        out = rec.invoke(
            {
                "path_summary": "DSR 诊断失败但仍可写回",
                "strategy_yaml": strategy_yaml,
                "metrics_json": '{"sharpe_ratio": 0.5}',
            }
        )
        assert "sid_exp_1" in out
        agent.context.memory.save_experience.assert_called_once()
        call_args = agent.context.memory.save_experience.call_args[0][0]
        assert call_args.metrics.get("outcome") == "success"

    def test_evidence_cleared_between_invocations(self, agent: ResearchAgent) -> None:
        """invoke 必须清空上一轮证据缓存，防止证据跨 invoke 泄漏绕过证据门

        评审 H17 修复：旧实现测试自己给 ``_evidence_cache`` 赋空后断言为空，
        生产清空逻辑从未被调用（构造性恒真）。此处用文件既有 mock 模式
        （mock 底层 ReAct agent，不跑真实 LLM）真实调用生产 ``invoke()``：
        - ``_evidence_cache`` / ``_beam_paths`` 由生产代码重置；
        - 重置后 ``record_path_outcome`` 无法再复用上一轮证据写回 success；
        - ``_strategy_trial_count`` 是 DSR 跨 invoke 的 session 级计数
          （见 research_agent.py「本 session 已探索策略数」声明），invoke
          不重置——每 invoke 重置会低估 trial 数、放水统计门。
        """
        strategy_yaml = self._valid_strategy()
        bt = next(t for t in agent._build_tools() if t.name == "run_backtest")
        bt.invoke({"strategy_yaml": strategy_yaml})
        assert len(agent._evidence_cache) == 1
        assert agent._strategy_trial_count == 1

        # 真实调用生产 invoke()（底层 ReAct agent mock 返回空消息，无 LLM 调用）
        agent._agent.invoke.return_value = {"messages": []}
        agent.invoke("研发动量策略")

        # 生产重置逻辑生效：证据缓存与 beam 路径被清空
        assert agent._evidence_cache == {}, "invoke 应清空上一轮证据缓存"
        assert agent._beam_paths == [], "invoke 应清空上一轮 beam 路径"
        # session 级 DSR 计数跨 invoke 保留（生产声明的语义）
        assert agent._strategy_trial_count == 1, (
            "_strategy_trial_count 为 session 级 DSR 计数，invoke 不应重置"
        )

        # 行为后果：上一轮的证据不能再为 success 写回背书（证据门未被绕过）
        rec = next(t for t in agent._build_tools() if t.name == "record_path_outcome")
        out = rec.invoke(
            {
                "path_summary": "上一轮已取证策略",
                "strategy_yaml": strategy_yaml,
                "metrics_json": '{"sharpe_ratio": 1.0}',
            }
        )
        assert "拒绝写回成功" in out, (
            "invoke 清空证据后，上一轮证据不应再支持 success 写回"
        )

    def test_current_best_oos_updates_and_pbo_runs(
        self, agent: ResearchAgent
    ) -> None:
        """第二次更高 OOS mean 更新 _current_best_oos；候选≥2 时 PBO 非 skipped。"""
        strategy_yaml_a = self._valid_strategy()
        strategy_yaml_b = (
            "name: quality_momentum_v2\n"
            "universe:\n"
            "  type: main_board+gem\n"
            "signals:\n"
            "  - type: operator\n"
            "    op: rank_top\n"
            "    params:\n"
            "      n: 20\n"
        )
        oos = next(t for t in agent._build_tools() if t.name == "run_oos_gates")
        daily_returns_a = [0.001 * (i % 5 - 2) for i in range(20)]
        daily_returns_b = [0.002 * (i % 7 - 3) for i in range(20)]

        agent.context.backtest_service.run.return_value = {
            "sharpe_ratio": 0.5,
            "total_return": 0.1,
            "trade_count": 42,
            "metrics": {"sharpe_ratio": 0.5, "total_return": 0.1},
            "strategy_diagnostics": {"degenerate": False, "trade_count": 42},
            "daily_returns": daily_returns_a,
        }
        agent.context.backtest_service.run_oos.return_value = {
            "fold_results": [
                {"test": {"sharpe_ratio": 0.5}, "trading_days": 63},
                {"test": {"sharpe_ratio": 0.4}, "trading_days": 63},
            ],
        }
        first = json.loads(oos.invoke({"strategy_yaml": strategy_yaml_a}))
        assert first["passed"] is True
        assert agent._current_best_oos == pytest.approx(0.45)
        assert first["pbo"]["status"] == "skipped"
        assert "method" in first["pbo"]

        agent.context.backtest_service.run.return_value = {
            "sharpe_ratio": 0.6,
            "total_return": 0.12,
            "trade_count": 42,
            "metrics": {"sharpe_ratio": 0.6, "total_return": 0.12},
            "strategy_diagnostics": {"degenerate": False, "trade_count": 42},
            "daily_returns": daily_returns_b,
        }
        agent.context.backtest_service.run_oos.return_value = {
            "fold_results": [
                {"test": {"sharpe_ratio": 0.8}, "trading_days": 63},
                {"test": {"sharpe_ratio": 0.7}, "trading_days": 63},
            ],
        }
        second = json.loads(oos.invoke({"strategy_yaml": strategy_yaml_b}))
        assert second["passed"] is True
        assert agent._current_best_oos == pytest.approx(0.75)
        assert second["pbo"]["status"] != "skipped"
        assert second["pbo"]["skipped"] is False
        assert second["pbo"]["method"] in ("cscv_matrix", "pair_legacy")
        agent.context.backtest_service.run_oos.assert_called()
        oos_call_kwargs = agent.context.backtest_service.run_oos.call_args.kwargs
        assert oos_call_kwargs.get("gap") == 5
