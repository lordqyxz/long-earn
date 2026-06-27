"""HTR 子图测试（ADR-010 Phase 2）。

核心信任路径：子图编译 + _decide_node 合并门逻辑。
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
        result = _decide_node(state, agent, backtest_service, logger=None)  # type: ignore[arg-type]
        assert result["result"] == "stop"

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
        result = _decide_node(state, agent, backtest_service, logger=None)  # type: ignore[arg-type]
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
            {"run_id": "old_run", "best_insight": "动量过滤有效", "best_direction": "收益增强"}
        ]

        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )
        agent = StrategyResearchAgent(context=context)

        _ideate_node(
            {"hypothesis_tree": tree.serialize(), "result": "测试观察"},
            research_agent=agent,
            memory=memory,
            logger=None,  # type: ignore[arg-type]
        )

        # search_hypothesis_trees 应被调用（hot-start）
        assert memory.search_hypothesis_trees.called


class TestPhase5ParallelDispatch:
    """Phase 5: Send fan-out 并行分发。"""

    def test_dispatch_cond_single_returns_executor(self):
        """单假设时 _dispatch_cond 返回 'executor'（串行）。"""
        from long_earn.strategy_rd.htr_subgraph import _dispatch_cond

        state = {"selected_leaves": ["node_1"]}
        result = _dispatch_cond(state)  # type: ignore[arg-type]
        assert result == "executor"

    def test_dispatch_cond_multi_returns_send_list(self):
        """多假设时 _dispatch_cond 返回 Send 列表（并行 fan-out）。"""
        from langgraph.types import Send

        from long_earn.strategy_rd.htr_subgraph import _dispatch_cond

        state = {"selected_leaves": ["node_1", "node_2", "node_3"]}
        result = _dispatch_cond(state)  # type: ignore[arg-type]
        assert isinstance(result, list)
        assert len(result) == 3
        assert all(isinstance(s, Send) for s in result)

    def test_subgraph_with_parallel_compiles(self):
        """含 executor_single 节点的子图应能编译。"""
        from long_earn.strategy_rd.htr_subgraph import create_htr_subgraph

        context = _make_mock_context()
        subgraph = create_htr_subgraph(context)
        assert subgraph is not None
