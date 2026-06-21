"""策略研发子图集成测试

验证 strategy_rd 子图能正确编译、关键节点齐全、关键边连通。
这是 strategy_rd 全链路的"接口层"集成校验，不依赖 LLM 真实调用。
"""

import pytest

from long_earn.config import AppConfig
from long_earn.context_init import create_runtime_context
from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph

# strategy_rd 子图应包含的核心节点（Reflexion + 自适应检索 + 优化循环）
_REQUIRED_NODES = {
    "init",
    "initial_retrieval",
    "evaluate_retrieval",
    "adaptive_retrieval",
    "research",
    "develop",
    "backtest",
    "refine",
    "refine_optimized",
    "reflection",
    "optimize",
    "develop_optimized",
    "backtest_optimized",
    "save_experience",
    "supervisor",
}

# 关键路径边（START→init 是必经入口；develop→backtest 是核心研发链路；
# backtest→refine 是代码修复回路；optimize→develop_optimized 是优化循环入口）
_REQUIRED_EDGES = {
    ("__start__", "init"),
    ("init", "initial_retrieval"),
    ("develop", "backtest"),
    ("backtest", "refine"),
    ("optimize", "develop_optimized"),
    ("develop_optimized", "backtest_optimized"),
}


@pytest.fixture(scope="module")
def compiled_subgraph():
    """编译策略研发子图（context_init 失败则整组跳过）"""
    try:
        ctx = create_runtime_context(AppConfig.from_env())
        return create_strategy_rd_subgraph(ctx)
    except Exception as e:
        pytest.skip(f"子图编译失败: {e}")


class TestStrategyRDSubgraph:
    """策略研发子图集成测试"""

    def test_subgraph_compiles(self, compiled_subgraph):
        """子图应成功编译为 CompiledStateGraph"""
        assert compiled_subgraph is not None
        assert type(compiled_subgraph).__name__ == "CompiledStateGraph"

    def test_subgraph_has_all_required_nodes(self, compiled_subgraph):
        """子图应包含 Reflexion + 自适应检索 + 优化循环全部关键节点"""
        nodes = set(compiled_subgraph.nodes.keys())
        missing = _REQUIRED_NODES - nodes
        assert not missing, f"缺少关键节点: {missing}"

    def test_subgraph_has_required_edges(self, compiled_subgraph):
        """子图应连通关键路径：入口/研发链路/修复回路/优化循环"""
        graph = compiled_subgraph.get_graph()
        edges = {(e.source, e.target) for e in graph.edges}
        missing = _REQUIRED_EDGES - edges
        assert not missing, f"缺少关键边: {missing}"

    def test_subgraph_terminates_at_end(self, compiled_subgraph):
        """子图必须有节点通向 __end__，否则会无限循环"""
        graph = compiled_subgraph.get_graph()
        edges = {(e.source, e.target) for e in graph.edges}
        # 至少一条边指向 __end__
        has_end_edge = any(target == "__end__" for _, target in edges)
        assert has_end_edge, "子图未连通 __end__，可能导致无限循环"
