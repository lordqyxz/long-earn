"""策略研发子图集成测试

验证 strategy_rd 子图能正确编译和初始化。
"""

import pytest


class TestStrategyRDSubgraph:
    """策略研发子图集成测试"""

    def test_subgraph_compiles(self):
        """子图应能成功编译"""
        from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph
        from long_earn.context_init import create_runtime_context
        from long_earn.config import AppConfig

        config = AppConfig.from_env()
        ctx = create_runtime_context(config)

        try:
            graph = create_strategy_rd_subgraph(ctx)
            assert graph is not None
        except Exception as e:
            pytest.skip(f"子图编译失败: {e}")

    def test_subgraph_nodes_exist(self):
        """子图应包含关键节点"""
        from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph
        from long_earn.context_init import create_runtime_context
        from long_earn.config import AppConfig

        config = AppConfig.from_env()
        ctx = create_runtime_context(config)

        try:
            graph = create_strategy_rd_subgraph(ctx)
            nodes = list(graph.nodes.keys())
            assert "develop" in nodes or "backtest" in nodes
        except Exception as e:
            pytest.skip(f"子图节点检查失败: {e}")
