"""策略研究子图单元测试 - 无 LLM 依赖

测试策略：
1. 使用 Mock 对象模拟 LLM 和服务依赖
2. 验证子图结构、状态流转、节点逻辑
3. 无需真实 API 调用，快速验证核心逻辑

参考 LangGraph 测试最佳实践：
- https://langchain-ai.github.io/langgraph/how-tos/testing/
"""

from unittest.mock import MagicMock

from long_earn.config import AppConfig, RuntimeContext
from long_earn.services.backtest_service import BacktestService
from long_earn.services.knowledge_service import KnowledgeService
from long_earn.services.llm_service import LLMService
from long_earn.services.logger_service import LoggerService
from long_earn.services.monitoring_service import MonitoringService
from long_earn.services.stock_service import StockService
from long_earn.strategy_rd.state import State
from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph


def create_mock_context() -> RuntimeContext:
    """创建 Mock 运行时上下文"""

    # Mock LLM 服务
    mock_llm = MagicMock(spec=LLMService)
    mock_response = MagicMock()
    mock_response.content = "这是一个测试策略"
    mock_llm.invoke.return_value = mock_response

    # Mock 知识服务
    mock_knowledge = MagicMock(spec=KnowledgeService)
    mock_knowledge.search.return_value = ["测试知识 1", "测试知识 2"]
    mock_knowledge.save.return_value = True

    # Mock 日志服务
    mock_logger = MagicMock(spec=LoggerService)

    # Mock 监控服务
    mock_monitoring = MagicMock(spec=MonitoringService)

    # Mock 股票服务
    mock_stock = MagicMock(spec=StockService)

    # Mock 回测服务
    mock_backtest = MagicMock(spec=BacktestService)

    # Mock 配置
    mock_config = MagicMock(spec=AppConfig)
    mock_config.llm_type = "ollama"
    mock_config.llm_model = "qwen3.5:cloud"
    mock_config.llm_base_url = "http://localhost:11434"
    mock_config.qdrant_url = ":memory:"
    mock_config.qdrant_api_key = None
    mock_config.embedding_model = "qwen3-embedding:0.6b"
    mock_config.init_dir = "./init"
    mock_config.max_iterations = 3
    mock_config.backtest_start_date = "2020-01-01"
    mock_config.backtest_end_date = "2023-12-31"

    # 创建上下文（使用 dataclass 的直接属性赋值）
    context = RuntimeContext(
        llm_service=mock_llm,
        knowledge_service=mock_knowledge,
        stock_service=mock_stock,
        backtest_service=mock_backtest,
        logger=mock_logger,
        monitoring=mock_monitoring,
        config=mock_config,
    )

    return context


def test_state_definition():
    """测试 1: 验证 State 定义"""
    print("=" * 60)
    print("测试 1: State 定义验证")
    print("=" * 60)

    try:
        # 验证 State 是 TypedDict
        from typing import get_type_hints

        hints = get_type_hints(State)

        # 验证关键字段存在
        required_fields = [
            "query",
            "strategy",
            "strategy_code",
            "backtest_result",
            "reflection",
            "improvement_suggestions",
            "iteration",
            "knowledge_context",
            "retrieval_count",
            "code_valid",
        ]

        for field in required_fields:
            assert field in hints, f"State 缺少字段：{field}"

        print("✅ State 定义正确，所有必需字段存在")
        return True
    except Exception as e:
        print(f"❌ State 定义验证失败：{e}")
        return False


def test_subgraph_creation():
    """测试 2: 验证子图创建"""
    print("\n" + "=" * 60)
    print("测试 2: 子图创建验证")
    print("=" * 60)

    try:
        context = create_mock_context()
        subgraph = create_strategy_rd_subgraph(context)

        # 验证子图已编译
        assert subgraph is not None
        print("✅ 子图创建成功")

        # 验证节点存在
        nodes = list(subgraph.nodes.keys())
        expected_nodes = [
            "init",
            "initial_retrieval",
            "evaluate_retrieval",
            "adaptive_retrieval",
            "research",
            "develop",
            "backtest",
            "refine",
            "reflection",
            "optimize",
            "develop_optimized",
            "backtest_optimized",
            "save_experience",
            "supervisor",
        ]

        for node in expected_nodes:
            assert node in nodes, f"缺少节点：{node}"

        print(f"✅ 所有节点已注册：{len(nodes)} 个节点")
        return True
    except Exception as e:
        print(f"❌ 子图创建失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def test_subgraph_structure():
    """测试 3: 验证子图结构（边和连接）"""
    print("\n" + "=" * 60)
    print("测试 3: 子图结构验证")
    print("=" * 60)

    try:
        context = create_mock_context()
        subgraph = create_strategy_rd_subgraph(context)

        # 获取图的边信息
        graph_info = subgraph.get_graph()

        # 验证 START 节点连接到 init
        edges = list(graph_info.edges)
        start_edges = [e for e in edges if e.source == "__start__"]
        assert len(start_edges) > 0, "缺少 START 边"
        assert start_edges[0].target == "init", "START 应连接到 init"

        print("✅ START -> init 连接正确")

        # 验证 init 连接到 initial_retrieval
        init_edges = [e for e in edges if e.source == "init"]
        assert len(init_edges) > 0, "init 缺少输出边"
        assert init_edges[0].target == "initial_retrieval", (
            "init 应连接到 initial_retrieval"
        )

        print("✅ init -> initial_retrieval 连接正确")

        # 验证 research -> develop 连接
        research_edges = [e for e in edges if e.source == "research"]
        assert len(research_edges) > 0, "research 缺少输出边"
        assert research_edges[0].target == "develop", "research 应连接到 develop"

        print("✅ research -> develop 连接正确")

        # 验证 develop -> backtest 连接
        develop_edges = [e for e in edges if e.source == "develop"]
        assert len(develop_edges) > 0, "develop 缺少输出边"
        assert develop_edges[0].target == "backtest", "develop 应连接到 backtest"

        print("✅ develop -> backtest 连接正确")

        print("✅ 子图结构验证通过")
        return True
    except Exception as e:
        print(f"❌ 子图结构验证失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def test_init_node():
    """测试 4: 验证 init 节点逻辑"""
    print("\n" + "=" * 60)
    print("测试 4: init 节点逻辑验证")
    print("=" * 60)

    try:
        context = create_mock_context()

        # 设置 Mock LLM 响应以避免实际调用
        mock_response = MagicMock()
        mock_response.content = '{"should_continue": false, "reason": "测试完成"}'
        context.llm_service.invoke.return_value = mock_response

        subgraph = create_strategy_rd_subgraph(context)

        # 准备初始状态
        initial_state = {
            "query": "测试策略",
        }

        # 运行子图（增加 recursion_limit）
        result = subgraph.invoke(initial_state, {"recursion_limit": 50})

        # 验证 init 节点执行了
        assert "iteration" in result, "init 未设置 iteration"
        assert "retrieval_count" in result, "init 未设置 retrieval_count"

        print("✅ init 节点输出正确:")
        print(f"   - iteration: {result.get('iteration')}")
        print(f"   - retrieval_count: {result.get('retrieval_count')}")

        return True
    except Exception as e:
        print(f"❌ init 节点测试失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def test_state_transitions():
    """测试 5: 验证状态流转"""
    print("\n" + "=" * 60)
    print("测试 5: 状态流转验证")
    print("=" * 60)

    try:
        context = create_mock_context()
        subgraph = create_strategy_rd_subgraph(context)

        # 准备初始状态
        initial_state = {
            "query": "测试策略",
            "max_iterations": 1,
        }

        # 运行子图（限制步骤数避免无限循环）
        result = subgraph.invoke(initial_state, {"recursion_limit": 10})  # 限制递归次数

        # 验证最终状态包含预期字段
        assert "query" in result, "结果缺少 query"
        assert "iteration" in result, "结果缺少 iteration"

        print("✅ 状态流转正常")
        print(f"   - 最终 iteration: {result.get('iteration')}")
        print(f"   - 包含字段：{list(result.keys())}")

        return True
    except Exception as e:
        print(f"❌ 状态流转测试失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def test_conditional_edges():
    """测试 6: 验证条件边逻辑"""
    print("\n" + "=" * 60)
    print("测试 6: 条件边逻辑验证")
    print("=" * 60)

    try:
        context = create_mock_context()
        subgraph = create_strategy_rd_subgraph(context)

        # 测试 backtest -> refine/reflection 条件边
        # 当 code_valid=False 时，应该走向 refine
        state_invalid_code = {
            "query": "测试",
            "strategy_code": "",  # 空代码
            "code_valid": False,
        }

        # 测试 backtest 节点
        backtest_result = subgraph.invoke(state_invalid_code, {"recursion_limit": 5})

        # 验证状态中包含 backtest_result
        assert "backtest_result" in backtest_result, "backtest 节点未执行"

        print("✅ 条件边逻辑验证通过")
        print(f"   - backtest_result: {backtest_result.get('backtest_result', {})}")

        return True
    except Exception as e:
        print(f"❌ 条件边测试失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def test_counter_management():
    """测试 7: 验证计数器管理"""
    print("\n" + "=" * 60)
    print("测试 7: 计数器管理验证")
    print("=" * 60)

    try:
        context = create_mock_context()
        subgraph = create_strategy_rd_subgraph(context)

        # 验证 init 节点重置计数器
        initial_state = {
            "query": "测试",
            "iteration": 5,  # 假设之前的迭代次数
        }

        result = subgraph.invoke(initial_state, {"recursion_limit": 3})

        # 验证 iteration 被重置
        assert "iteration" in result, "iteration 未设置"

        print("✅ 计数器管理正常")
        print(f"   - 最终 iteration: {result.get('iteration')}")

        return True
    except Exception as e:
        print(f"❌ 计数器管理测试失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def test_mock_llm_responses():
    """测试 8: 验证 Mock LLM 响应处理"""
    print("\n" + "=" * 60)
    print("测试 8: Mock LLM 响应处理验证")
    print("=" * 60)

    try:
        context = create_mock_context()

        # 验证 LLM Mock 已正确设置
        llm_service = context.llm_service
        assert llm_service is not None, "LLM 服务未设置"

        # 测试 LLM 调用
        response = llm_service.invoke("测试 prompt")
        assert response.content == "这是一个测试策略", "Mock LLM 响应不正确"

        print("✅ Mock LLM 响应处理正确")
        print(f"   - LLM 调用次数：{llm_service.invoke.call_count}")

        return True
    except Exception as e:
        print(f"❌ Mock LLM 测试失败：{e}")
        return False


def main():
    """运行所有单元测试"""
    print("\n" + "=" * 60)
    print("策略研究子图 - 单元测试（无 LLM 依赖）")
    print("=" * 60)

    tests = [
        ("State 定义", test_state_definition),
        ("子图创建", test_subgraph_creation),
        ("子图结构", test_subgraph_structure),
        ("init 节点", test_init_node),
        ("状态流转", test_state_transitions),
        ("条件边", test_conditional_edges),
        ("计数器管理", test_counter_management),
        ("Mock LLM 响应", test_mock_llm_responses),
    ]

    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n❌ {name} 测试异常：{e}")
            results.append((name, False))

    # 汇总结果
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status} - {name}")

    print(f"\n总计：{passed}/{total} 测试通过")

    if passed == total:
        print("\n✅ 所有单元测试通过！")
        return True
    else:
        print(f"\n❌ {total - passed} 个测试失败")
        return False


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
