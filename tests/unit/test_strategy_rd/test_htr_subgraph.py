"""HTR 子图测试（ADR-010 Phase 2）。

核心信任路径：子图编译 + _decide_node 合并门逻辑。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from long_earn.config import RuntimeContext
from long_earn.services import MemoryService
from long_earn.services.backtest_service import BacktestService
from long_earn.services.llm_service import LLMService
from long_earn.services.logger_service import LoggerService
from long_earn.services.monitoring_service import MonitoringService
from long_earn.services.stock_service import StockService
from long_earn.skills.personas.protocol import PersonaContext, PersonaResult
from long_earn.strategy_rd.hypothesis_tree import HypothesisTree


def _make_mock_context() -> RuntimeContext:
    """创建带 mock 服务的 RuntimeContext。"""
    mock_llm = MagicMock(spec=LLMService)
    mock_response = MagicMock()
    mock_response.content = '{"action": "continue", "reason": "test"}'
    mock_llm.invoke.return_value = mock_response

    mock_memory = MagicMock(spec=MemoryService)
    mock_memory.save_experience.return_value = "test-id"
    mock_memory.search_experience.return_value = []

    mock_config = MagicMock()
    mock_config.llm_type = "ollama"
    mock_config.llm_model = "test"
    mock_config.llm_base_url = "http://localhost"
    mock_config.memory_path = ":memory:"
    mock_config.init_dir = "./init"
    mock_config.max_iterations = 1
    mock_config.htr_max_select = 1
    # ADR-010: htr_max_cycles 从 config 注入 _decide_node（必须为 int，否则 max() 报错）
    mock_config.htr_max_cycles = 10
    mock_config.backtest_start_date = "2020-01-01"
    mock_config.backtest_end_date = "2023-12-31"
    mock_config.train_start_date = "2022-01-01"
    mock_config.train_end_date = "2024-12-31"
    mock_config.test_start_date = "2025-01-01"
    mock_config.test_end_date = "2026-03-24"
    mock_config.validation_start_date = "2026-03-25"
    mock_config.validation_end_date = "2026-06-25"

    return RuntimeContext(
        llm_service=mock_llm,
        memory=mock_memory,
        stock_service=MagicMock(spec=StockService),
        backtest_service=MagicMock(spec=BacktestService),
        logger=MagicMock(spec=LoggerService),
        monitoring=MagicMock(spec=MonitoringService),
        config=mock_config,
    )


class TestHTRSubgraphCompiles:
    def test_subgraph_compiles(self):
        """HTR 子图应能成功编译。"""
        from long_earn.strategy_rd.htr_subgraph import create_htr_subgraph

        context = _make_mock_context()
        subgraph = create_htr_subgraph(context)
        assert subgraph is not None


class TestDecideNodeLogic:
    """_decide_node 的合并门逻辑——核心信任路径。"""

    def test_max_cycles_forces_stop(self):
        """达到最大周期时必须强制停止。"""
        from long_earn.strategy_rd.htr_subgraph import _decide_node

        tree = HypothesisTree(run_id="test")
        tree.init_root()

        context = _make_mock_context()
        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        agent = StrategyResearchAgent(context=context)
        backtest_service = context.require_backtest()

        state = {
            "hypothesis_tree": tree.serialize(),
            "iteration": 100,  # 超过 HTR_MAX_CYCLES=10
            "executor_results": [],
        }
        result = _decide_node(
            state, agent, backtest_service, connector=None, logger=None
        )  # type: ignore[arg-type]
        assert result["result"] == "stop"

    def test_max_cycles_config_override(self):
        """max_cycles 参数应能覆盖默认 HTR_MAX_CYCLES。

        验证 ADR-010 修复：htr_max_cycles 不再硬编码，从 config 注入。
        设 max_cycles=3，iteration=3 应触发停止（默认 10 不会停止）。
        """
        from long_earn.strategy_rd.htr_subgraph import _decide_node

        tree = HypothesisTree(run_id="test_cfg")
        tree.init_root()

        context = _make_mock_context()
        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        agent = StrategyResearchAgent(context=context)
        backtest_service = context.require_backtest()

        # iteration=3 + max_cycles=3 → 应停止（iteration >= max_cycles）
        state = {
            "hypothesis_tree": tree.serialize(),
            "iteration": 3,
            "executor_results": [],
        }
        result = _decide_node(
            state,
            agent,
            backtest_service,
            connector=None,
            logger=None,  # type: ignore[arg-type]
            max_cycles=3,
        )
        assert result["result"] == "stop"

    def test_subgraph_reads_htr_max_cycles_from_config(self):
        """create_htr_subgraph 应从 config.htr_max_cycles 读取最大周期数。"""
        from long_earn.strategy_rd.htr_subgraph import create_htr_subgraph

        context = _make_mock_context()
        # 修改 htr_max_cycles 为较小值，验证子图仍能编译
        context.config.htr_max_cycles = 5
        subgraph = create_htr_subgraph(context)
        assert subgraph is not None

    def test_max_depth_forces_stop(self):
        """达到最大深度时必须强制停止。"""
        from long_earn.strategy_rd.htr_subgraph import _decide_node

        tree = HypothesisTree(run_id="test")
        tree.init_root()
        # 添加深度超过 HTR_MAX_DEPTH=3 的节点
        parent = "root"
        for i in range(5):
            parent = tree.add_child(parent, f"假设_{i}")

        context = _make_mock_context()
        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        agent = StrategyResearchAgent(context=context)
        backtest_service = context.require_backtest()

        state = {
            "hypothesis_tree": tree.serialize(),
            "iteration": 0,
            "executor_results": [],
        }
        result = _decide_node(
            state, agent, backtest_service, connector=None, logger=None
        )  # type: ignore[arg-type]
        assert result["result"] == "stop"


class TestPhase4MemoryIntegration:
    """Phase 4: 树摘要回写 SubstanceStore + hot-start 检索。"""

    def test_save_tree_writes_memory(self):
        """_save_tree_node 应调用 memory.save_hypothesis_tree。"""
        from long_earn.strategy_rd.htr_subgraph import _save_tree_node
        from long_earn.strategy_rd.hypothesis_tree import HypothesisTree

        tree = HypothesisTree(run_id="test_p4")
        tree.init_root(hypothesis="初始策略")
        tree.add_child("root", "假设A")

        context = _make_mock_context()
        memory = context.require_memory()

        _save_tree_node(
            {"hypothesis_tree": tree.serialize()},
            memory=memory,
            logger=None,  # type: ignore[arg-type]
        )

        # save_hypothesis_tree 应被调用
        assert memory.save_hypothesis_tree.called

    def test_ideate_uses_memory_hotstart(self):
        """_ideate_node 应检索历史树洞察注入假设生成。"""
        from long_earn.strategy_rd.htr_subgraph import _ideate_node
        from long_earn.strategy_rd.hypothesis_tree import HypothesisTree

        tree = HypothesisTree(run_id="test_p4_ideate")
        tree.init_root(hypothesis="动量策略")

        context = _make_mock_context()
        memory = context.require_memory()
        # 模拟历史树摘要返回
        memory.search_hypothesis_trees.return_value = [
            {
                "run_id": "old_run",
                "best_insight": "动量过滤有效",
                "best_direction": "收益增强",
            }
        ]

        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        agent = StrategyResearchAgent(context=context)

        _ideate_node(
            {"hypothesis_tree": tree.serialize(), "result": "测试观察"},
            research_agent=agent,
            memory=memory,
            connector=None,
            logger=None,  # type: ignore[arg-type]
        )

        # search_hypothesis_trees 应被调用（hot-start）
        assert memory.search_hypothesis_trees.called


class TestPhase5ParallelDispatch:
    """ADR-010 阶段 5 收尾（2026-08）：Send fan-out 已移除，dispatch 始终走 executor。"""

    def test_dispatch_cond_single_returns_executor(self):
        """单假设时 _dispatch_cond 返回 'executor'。"""
        from long_earn.strategy_rd.htr_subgraph import _dispatch_cond

        state = {"selected_leaves": ["node_1"]}
        result = _dispatch_cond(state)  # type: ignore[arg-type]
        assert result == "executor"

    def test_dispatch_cond_multi_always_returns_executor(self):
        """多假设时 _dispatch_cond 仍返回 'executor'（批量并行在 executor 内部）。"""
        from long_earn.strategy_rd.htr_subgraph import _dispatch_cond

        state = {"selected_leaves": ["node_1", "node_2", "node_3"]}
        result = _dispatch_cond(state)  # type: ignore[arg-type]
        assert result == "executor"

    def test_subgraph_without_executor_single_compiles(self):
        """删除 executor_single 节点后子图仍能编译。"""
        from long_earn.strategy_rd.htr_subgraph import create_htr_subgraph

        context = _make_mock_context()
        subgraph = create_htr_subgraph(context)
        assert subgraph is not None


class TestMaxSelectFanOut:
    """ADR-010 Phase 5: max_select 可配置，>1 激活并行 fan-out。"""

    def test_select_node_max_select_2_adds_two_nodes(self):
        """max_select=2 时 _select_node 应向树添加 2 个子节点。"""
        from long_earn.strategy_rd.htr_subgraph import _select_node

        tree = HypothesisTree(run_id="test_fanout_2")
        tree.init_root(hypothesis="父假设")

        research_agent = MagicMock()
        # 模拟 select 返回 2 个假设
        research_agent.select.return_value = [
            {"hypothesis": "假设A", "direction": "动量"},
            {"hypothesis": "假设B", "direction": "均值回归"},
        ]

        state = {
            "hypothesis_tree": tree.serialize(),
            "improvement_suggestions": ["假设A", "假设B", "假设C"],
        }
        result = _select_node(
            state,  # type: ignore[arg-type]
            research_agent=research_agent,
            logger=None,
            max_select=2,
        )

        # research_agent.select 应被调用且 max_select=2 转发
        research_agent.select.assert_called_once()
        call_kwargs = research_agent.select.call_args.kwargs
        assert call_kwargs.get("max_select") == 2

        # 树应含 root + 2 个子节点
        assert len(result["selected_leaves"]) == 2
        updated_tree = HypothesisTree.deserialize(result["hypothesis_tree"])
        root_children = [n for n in updated_tree.all_nodes() if n.parent_id == "root"]
        assert len(root_children) == 2

    def test_select_node_max_select_1_serial_behavior(self):
        """max_select=1 时 _select_node 保持串行行为（向后兼容）。"""
        from long_earn.strategy_rd.htr_subgraph import _select_node

        tree = HypothesisTree(run_id="test_serial")
        tree.init_root(hypothesis="父假设")

        research_agent = MagicMock()
        research_agent.select.return_value = [
            {"hypothesis": "唯一假设", "direction": "动量"},
        ]

        state = {
            "hypothesis_tree": tree.serialize(),
            "improvement_suggestions": ["假设A", "假设B"],
        }
        result = _select_node(
            state,  # type: ignore[arg-type]
            research_agent=research_agent,
            logger=None,
            max_select=1,
        )

        assert len(result["selected_leaves"]) == 1
        call_kwargs = research_agent.select.call_args.kwargs
        assert call_kwargs.get("max_select") == 1

    def test_fanout_flow_select_then_dispatch(self):
        """端到端：select 2 个 -> dispatch_cond 始终返回 executor（批量在内部）。"""
        from long_earn.strategy_rd.htr_subgraph import (
            _dispatch_cond,
            _select_node,
        )

        tree = HypothesisTree(run_id="test_flow")
        tree.init_root(hypothesis="父假设")

        research_agent = MagicMock()
        research_agent.select.return_value = [
            {"hypothesis": "假设A", "direction": "动量"},
            {"hypothesis": "假设B", "direction": "均值回归"},
        ]

        state = {
            "hypothesis_tree": tree.serialize(),
            "improvement_suggestions": ["假设A", "假设B"],
        }
        select_result = _select_node(
            state,  # type: ignore[arg-type]
            research_agent=research_agent,
            logger=None,
            max_select=2,
        )

        # 将 select 结果送入 dispatch_cond - 始终返回 "executor"
        dispatch_state = {"selected_leaves": select_result["selected_leaves"]}
        dispatch_result = _dispatch_cond(dispatch_state)  # type: ignore[arg-type]
        assert dispatch_result == "executor"

    def test_subgraph_reads_htr_max_select_from_config(self):
        """create_htr_subgraph 应从 config.htr_max_select 读取并行度。"""
        from long_earn.strategy_rd.htr_subgraph import create_htr_subgraph

        context = _make_mock_context()
        # 修改 htr_max_select 为 2
        context.config.htr_max_select = 2
        subgraph = create_htr_subgraph(context)
        assert subgraph is not None


class TestParallelFanOutGraphInvoke:
    """ADR-010 阶段 5 收尾（2026-08）：executor 内部批量并行回归。

    验证 state.py 的 _collect_executor_results reducer 生效
    + executor 节点不写 tree + backpropagate 接管 tree 更新。
    """

    def test_parallel_fanout_invoke_no_crash_and_tree_updated(self):
        """max_select=2 时 graph.invoke 不崩溃，executor_results 累加 2 项，tree 更新。"""
        from long_earn.strategy_rd.htr_subgraph import create_htr_subgraph
        from long_earn.strategy_rd.hypothesis_tree import (
            HypothesisTree,
            NodeStatus,
        )

        context = _make_mock_context()
        context.config.htr_max_select = 2
        # ADR-010 阶段 5 收尾：executor 调 run_candidates 批量回测（非 run）
        context.backtest_service.run_candidates.return_value = [
            {
                "sharpe_ratio": 1.3,
                "total_return": 0.1,
                "strategy_diagnostics": {"degenerate": False},
            },
            {
                "sharpe_ratio": 1.3,
                "total_return": 0.1,
                "strategy_diagnostics": {"degenerate": False},
            },
        ]
        # _decide_node 会调 run_oos 取 oos_sharpe，必须返回真实 dict 否则 MagicMock 触发 format 错误
        context.backtest_service.run_oos.return_value = {"oos_sharpe": 1.5}

        # 拦截所有 LLM 依赖路径，让 graph.invoke 快速跑完一轮
        with (
            patch(
                "long_earn.strategy_rd.htr_subgraph.PersonaRegistry.create_all",
                return_value={},  # 跳过大师 LLM
            ),
            patch(
                "long_earn.strategy_rd.htr_subgraph.HypothesisTreeStore.save",
                return_value=None,  # 避免沙箱磁盘写入
            ),
            patch(
                "long_earn.strategy_rd.agents.strategy_research_agent.StrategyResearchAgent.observe",
                return_value={"observation": "测试观察"},
            ),
            patch(
                "long_earn.strategy_rd.agents.strategy_research_agent.StrategyResearchAgent.ideate",
                return_value=[{"hypothesis": "假设A"}, {"hypothesis": "假设B"}],
            ),
            patch(
                "long_earn.strategy_rd.agents.strategy_research_agent.StrategyResearchAgent.select",
                return_value=[
                    {"hypothesis": "假设A", "direction": "动量"},
                    {"hypothesis": "假设B", "direction": "反转"},
                ],
            ),
            patch(
                "long_earn.strategy_rd.agents.strategy_research_agent.StrategyResearchAgent.optimize_strategy",
                return_value={"name": "optimized"},
            ),
            patch(
                "long_earn.strategy_rd.agents.strategy_develop_agent.StrategyDevelopAgent.develop_strategy",
                return_value="strategy:\n  name: opt\n",
            ),
            patch(
                "long_earn.strategy_rd.agents.strategy_research_agent.StrategyResearchAgent.backpropagate_insights",
                return_value={"insight": "测试洞察"},
            ),
            patch(
                "long_earn.strategy_rd.agents.strategy_research_agent.StrategyResearchAgent.decide",
                return_value={"action": "stop"},  # ADR-015 B4: decide 返回 dict
            ),
        ):
            graph = create_htr_subgraph(context)
            result = graph.invoke({"query": "测试并行"}, config={"recursion_limit": 50})

        # 1. 不崩溃（走到这里即证明 InvalidUpdateError 已修复）
        # 2. executor_results 通过 reducer 累加 2 项
        exec_results = result.get("executor_results", [])
        assert len(exec_results) == 2, f"期望 2 个并行结果，实际 {len(exec_results)}"
        node_ids = {r.get("node_id") for r in exec_results}
        assert len(node_ids) == 2, "两个 result 应对应不同 node_id"

        # 3. hypothesis_tree 在 backpropagate 后含 2 个子节点，evidence 已更新
        #    backpropagate 标记 VALIDATED；_decide_node 跑 OOS 通过合并门会把
        #    最佳节点升级为 MERGED（run_oos mock 返回 oos_sharpe=1.5 > None+threshold）
        tree = HypothesisTree.deserialize(result["hypothesis_tree"])
        children = [n for n in tree.all_nodes() if n.parent_id == "root"]
        assert len(children) == 2, "tree 应含 2 个子节点"
        for child in children:
            assert child.dev_score == 1.3, f"子节点 {child.id} dev_score 应已更新"
            assert child.status in (NodeStatus.VALIDATED, NodeStatus.MERGED)
            assert child.insight == "测试洞察"


class TestPersonaIntegration:
    """ADR-012: HTR 集成 PersonaRegistry（ideate + backpropagate）。"""

    def test_ideate_node_calls_persona_registry(self):
        """_ideate_node 应调用 PersonaRegistry.create_all 生成大师建议。"""
        from long_earn.strategy_rd.htr_subgraph import _ideate_node

        tree = HypothesisTree(run_id="test_persona_ideate")
        tree.init_root(hypothesis="动量策略")

        context = _make_mock_context()
        memory = context.require_memory()
        memory.search_hypothesis_trees.return_value = []

        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        agent = StrategyResearchAgent(context=context)
        # 拦截 ideate 避免真实 LLM 调用
        agent.ideate = MagicMock(return_value=[{"hypothesis": "h1"}])

        # 构造 mock persona：analyze 返回 PersonaResult
        mock_persona = MagicMock()
        mock_persona.analyze.return_value = PersonaResult(
            verdict="推荐",
            rationale="测试建议",
            suggestions=["建议1"],
            confidence=0.8,
        )

        with patch(
            "long_earn.skills.personas.PersonaRegistry.create_all",
            return_value={"buffett": mock_persona},
        ):
            _ideate_node(
                {"hypothesis_tree": tree.serialize(), "result": "观察"},
                research_agent=agent,
                memory=memory,
                connector=None,
                logger=None,  # type: ignore[arg-type]
            )

        # PersonaRegistry.create_all 应被调用
        mock_persona.analyze.assert_called_once()
        # 调用时的 context.mode 应是 strategy_generate
        call_ctx = mock_persona.analyze.call_args.args[0]
        assert isinstance(call_ctx, PersonaContext)
        assert call_ctx.mode == "strategy_generate"

    def test_ideate_node_passes_master_hints_to_ideate(self):
        """_ideate_node 应把大师建议通过 master_hints 传给 ideate。"""
        from long_earn.strategy_rd.htr_subgraph import _ideate_node

        tree = HypothesisTree(run_id="test_hints")
        tree.init_root(hypothesis="动量策略")

        context = _make_mock_context()
        memory = context.require_memory()
        memory.search_hypothesis_trees.return_value = []

        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        agent = StrategyResearchAgent(context=context)
        agent.ideate = MagicMock(return_value=[])

        mock_persona = MagicMock()
        mock_persona.analyze.return_value = PersonaResult(
            verdict="推荐", rationale="大师建议"
        )

        with patch(
            "long_earn.skills.personas.PersonaRegistry.create_all",
            return_value={"buffett": mock_persona},
        ):
            _ideate_node(
                {"hypothesis_tree": tree.serialize(), "result": "观察"},
                research_agent=agent,
                memory=memory,
                connector=None,
                logger=None,  # type: ignore[arg-type]
            )

        # ideate 应被调用且 master_hints 含 buffett 条目
        agent.ideate.assert_called_once()
        master_hints_arg = agent.ideate.call_args.kwargs.get("master_hints")
        assert master_hints_arg is not None
        assert "buffett" in master_hints_arg

    def test_ideate_node_degrades_when_persona_fails(self):
        """PersonaRegistry 初始化失败时 _ideate_node 应降级为无大师建议。"""
        from long_earn.strategy_rd.htr_subgraph import _ideate_node

        tree = HypothesisTree(run_id="test_degrade")
        tree.init_root(hypothesis="动量策略")

        context = _make_mock_context()
        memory = context.require_memory()
        memory.search_hypothesis_trees.return_value = []

        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        agent = StrategyResearchAgent(context=context)
        agent.ideate = MagicMock(return_value=[])

        with patch(
            "long_earn.skills.personas.PersonaRegistry.create_all",
            side_effect=RuntimeError("注册表初始化失败"),
        ):
            # 不应抛异常
            _ideate_node(
                {"hypothesis_tree": tree.serialize(), "result": "观察"},
                research_agent=agent,
                memory=memory,
                connector=None,
                logger=None,  # type: ignore[arg-type]
            )

        # ideate 仍应被调用，master_hints 为 None（降级）
        agent.ideate.assert_called_once()
        master_hints_arg = agent.ideate.call_args.kwargs.get("master_hints")
        assert master_hints_arg is None

    def test_backpropagate_node_calls_persona_registry(self):
        """_backpropagate_node 应调用 PersonaRegistry 的 strategy_review mode。"""
        from long_earn.strategy_rd.htr_subgraph import _backpropagate_node

        tree = HypothesisTree(run_id="test_bp_persona")
        tree.init_root(hypothesis="父假设")
        child_id = tree.add_child("root", "子假设")

        # 给子节点添加回测结果
        tree.update_evidence(
            node_id=child_id, dev_score=0.5, backtest_result={"sharpe_ratio": 0.5}
        )

        context = _make_mock_context()
        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        agent = StrategyResearchAgent(context=context)
        agent.backpropagate_insights = MagicMock(return_value={"insight": "测试洞察"})

        mock_persona = MagicMock()
        mock_persona.analyze.return_value = PersonaResult(
            verdict="改进",
            rationale="大师反思",
            weaknesses=["弱点1"],
            suggestions=["建议1"],
        )

        state = {
            "hypothesis_tree": tree.serialize(),
            "executor_results": [
                {"node_id": child_id, "backtest_result": {"sharpe_ratio": 0.5}}
            ],
            "strategy": {"strategy_name": "TestStrategy"},
        }

        with patch(
            "long_earn.skills.personas.PersonaRegistry.create_all",
            return_value={"buffett": mock_persona},
        ):
            _backpropagate_node(
                state,  # type: ignore[arg-type]
                research_agent=agent,
                logger=None,  # type: ignore[arg-type]
            )

        mock_persona.analyze.assert_called_once()
        call_ctx = mock_persona.analyze.call_args.args[0]
        assert isinstance(call_ctx, PersonaContext)
        assert call_ctx.mode == "strategy_review"

    def test_backpropagate_node_passes_master_perspectives(self):
        """_backpropagate_node 应把大师视角通过 master_perspectives 传给 backpropagate_insights。"""
        from long_earn.strategy_rd.htr_subgraph import _backpropagate_node

        tree = HypothesisTree(run_id="test_bp_hints")
        tree.init_root(hypothesis="父假设")
        child_id = tree.add_child("root", "子假设")
        tree.update_evidence(
            node_id=child_id, dev_score=0.5, backtest_result={"sharpe_ratio": 0.5}
        )

        context = _make_mock_context()
        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        agent = StrategyResearchAgent(context=context)
        agent.backpropagate_insights = MagicMock(return_value={"insight": "测试洞察"})

        mock_persona = MagicMock()
        mock_persona.analyze.return_value = PersonaResult(
            verdict="改进", rationale="反思"
        )

        state = {
            "hypothesis_tree": tree.serialize(),
            "executor_results": [
                {"node_id": child_id, "backtest_result": {"sharpe_ratio": 0.5}}
            ],
            "strategy": {"strategy_name": "TestStrategy"},
        }

        with patch(
            "long_earn.skills.personas.PersonaRegistry.create_all",
            return_value={"munger": mock_persona},
        ):
            _backpropagate_node(
                state,  # type: ignore[arg-type]
                research_agent=agent,
                logger=None,  # type: ignore[arg-type]
            )

        agent.backpropagate_insights.assert_called_once()
        perspectives_arg = agent.backpropagate_insights.call_args.kwargs.get(
            "master_perspectives"
        )
        assert perspectives_arg is not None
        assert "munger" in perspectives_arg

    def test_backpropagate_node_degrades_when_persona_fails(self):
        """PersonaRegistry 失败时 _backpropagate_node 应降级为无大师视角。"""
        from long_earn.strategy_rd.htr_subgraph import _backpropagate_node

        tree = HypothesisTree(run_id="test_bp_degrade")
        tree.init_root(hypothesis="父假设")
        child_id = tree.add_child("root", "子假设")
        tree.update_evidence(
            node_id=child_id, dev_score=0.5, backtest_result={"sharpe_ratio": 0.5}
        )

        context = _make_mock_context()
        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        agent = StrategyResearchAgent(context=context)
        agent.backpropagate_insights = MagicMock(return_value={"insight": "降级洞察"})

        state = {
            "hypothesis_tree": tree.serialize(),
            "executor_results": [
                {"node_id": child_id, "backtest_result": {"sharpe_ratio": 0.5}}
            ],
            "strategy": {"strategy_name": "TestStrategy"},
        }

        with patch(
            "long_earn.skills.personas.PersonaRegistry.create_all",
            side_effect=RuntimeError("注册表初始化失败"),
        ):
            # 不应抛异常
            _backpropagate_node(
                state,  # type: ignore[arg-type]
                research_agent=agent,
                logger=None,  # type: ignore[arg-type]
            )

        # backpropagate_insights 仍应被调用，master_perspectives 为 None
        agent.backpropagate_insights.assert_called_once()
        perspectives_arg = agent.backpropagate_insights.call_args.kwargs.get(
            "master_perspectives"
        )
        assert perspectives_arg is None
