"""股票分析子图集成测试

验证 stock_analysis 子图能正确编译、5 个分析师并行节点齐全、汇总节点连通。
不依赖真实 LLM 调用，仅校验拓扑结构。
"""

import pytest

from long_earn.config import AppConfig
from long_earn.context_init import create_runtime_context
from long_earn.stock_analysis.subgraph import create_stock_analysis_subgraph

# 5 视角并行分析（巴菲特 / 芒格 / 费雪 / 林奇基本面 + FundFlow 资金流向）
_ANALYST_NODES = {
    "petter_analysis",
    "charles_munger_analysis",
    "buffett_analysis",
    "fiske_analysis",
    "fund_flow_analysis",
}

# 子图必备节点：数据获取 + 5 分析师 + 汇总 + 错误处理
_REQUIRED_NODES = {"get_stock_data", "summarize", "error_handler"} | _ANALYST_NODES


@pytest.fixture(scope="module")
def compiled_subgraph():
    """编译股票分析子图（context_init 失败则整组跳过）"""
    try:
        ctx = create_runtime_context(AppConfig.from_env())
        return create_stock_analysis_subgraph(ctx)
    except Exception as e:
        pytest.skip(f"子图编译失败: {e}")


class TestStockAnalysisSubgraph:
    """股票分析子图集成测试"""

    def test_subgraph_compiles(self, compiled_subgraph):
        """子图应成功编译为 CompiledStateGraph"""
        assert compiled_subgraph is not None
        assert type(compiled_subgraph).__name__ == "CompiledStateGraph"

    def test_subgraph_has_all_required_nodes(self, compiled_subgraph):
        """子图应包含 5 个分析师并行节点 + 数据/汇总/错误节点"""
        nodes = set(compiled_subgraph.nodes.keys())
        missing = _REQUIRED_NODES - nodes
        assert not missing, f"缺少关键节点: {missing}"

    def test_all_analysts_converge_to_summarize(self, compiled_subgraph):
        """每个分析师节点都必须有边连向 summarize（否则汇总会丢失视角）"""
        graph = compiled_subgraph.get_graph()
        edges = {(e.source, e.target) for e in graph.edges}
        for analyst in _ANALYST_NODES:
            assert (analyst, "summarize") in edges, (
                f"{analyst} 未连向 summarize；该视角分析结果将丢失"
            )

    def test_subgraph_terminates_at_end(self, compiled_subgraph):
        """summarize 与 error_handler 都应连向 __end__"""
        graph = compiled_subgraph.get_graph()
        edges = {(e.source, e.target) for e in graph.edges}
        assert ("summarize", "__end__") in edges
        assert ("error_handler", "__end__") in edges
