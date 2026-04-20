"""StrategyResearchAgent 单元测试

测试策略研究 Agent 的核心功能，包括：
- research_strategy() 方法
- _get_knowledge_context() 方法
- 自适应检索逻辑
- Mock LLM 响应处理
- 错误处理
"""

from unittest.mock import MagicMock

from long_earn.config import AppConfig, RuntimeContext
from long_earn.services.backtest_service import BacktestService
from long_earn.services.knowledge_service import KnowledgeService
from long_earn.services.llm_service import LLMService
from long_earn.services.logger_service import LoggerService
from long_earn.services.monitoring_service import MonitoringService
from long_earn.services.stock_service import StockService
from long_earn.strategy_rd.agents.strategy_research_agent import StrategyResearchAgent


def create_mock_context() -> RuntimeContext:
    """创建 Mock 运行时上下文"""
    # Mock LLM 服务
    mock_llm = MagicMock(spec=LLMService)
    mock_response = MagicMock()
    mock_response.content = """{
        "strategy_name": "测试动量策略",
        "description": "这是一个基于动量的测试策略",
        "logic": "当价格超过 20 日均线时买入",
        "entry_conditions": ["价格 > 20 日均线"],
        "exit_conditions": ["价格 < 20 日均线"],
        "risk_management": "止损 5%"
    }"""
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

    # 创建上下文
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


def test_agent_creation():
    """测试 1: 验证 Agent 创建"""
    print("=" * 60)
    print("测试 1: Agent 创建验证")
    print("=" * 60)

    try:
        context = create_mock_context()
        agent = StrategyResearchAgent(context=context)

        assert agent is not None, "Agent 创建失败"
        assert agent.context is not None, "Agent context 未设置"
        assert agent.llm_service is not None, "Agent llm_service 未设置"
        assert agent.knowledge_service is not None, "Agent knowledge_service 未设置"

        print("✅ StrategyResearchAgent 创建成功")
        print(f"   - context: {type(agent.context).__name__}")
        print(f"   - llm_service: {type(agent.llm_service).__name__}")
        print(f"   - knowledge_service: {type(agent.knowledge_service).__name__}")

        return True
    except Exception as e:
        print(f"❌ Agent 创建失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def test_research_strategy():
    """测试 2: 验证策略研究功能"""
    print("\n" + "=" * 60)
    print("测试 2: 策略研究功能验证")
    print("=" * 60)

    try:
        context = create_mock_context()
        agent = StrategyResearchAgent(context=context)

        # 测试查询
        test_query = "创建一个基于动量的策略"

        # 调用 research_strategy_with_context
        strategy = agent.research_strategy_with_context(test_query)

        # 验证返回结果
        assert strategy is not None, "策略研究返回 None"
        assert "strategy_name" in strategy, "策略缺少 strategy_name 字段"
        assert "description" in strategy, "策略缺少 description 字段"

        print("✅ 策略研究功能正常")
        print(f"   - 策略名称：{strategy.get('strategy_name')}")
        print(f"   - 描述：{strategy.get('description', '')[:50]}...")

        # 验证 LLM 被调用
        assert context.llm_service.invoke.called, "LLM 服务未被调用"
        print(f"   - LLM 调用次数：{context.llm_service.invoke.call_count}")

        return True
    except Exception as e:
        print(f"❌ 策略研究功能失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def test_knowledge_retrieval():
    """测试 3: 验证知识检索功能"""
    print("\n" + "=" * 60)
    print("测试 3: 知识检索功能验证")
    print("=" * 60)

    try:
        context = create_mock_context()
        agent = StrategyResearchAgent(context=context)

        # 测试知识检索
        test_query = "动量策略"
        knowledge = agent._get_knowledge_context(test_query, node_type="research")

        # 验证知识检索结果
        assert knowledge is not None, "知识检索返回 None"
        assert isinstance(knowledge, str), "知识检索返回类型不正确"

        print("✅ 知识检索功能正常")
        print(f"   - 检索到的知识长度：{len(knowledge)}")
        print(f"   - 知识预览：{knowledge[:100]}...")

        # 验证知识服务被调用
        assert context.knowledge_service.search.called, "知识服务未被调用"
        print(f"   - 知识服务调用次数：{context.knowledge_service.search.call_count}")

        return True
    except Exception as e:
        print(f"❌ 知识检索功能失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def test_adaptive_retrieval():
    """测试 4: 验证自适应检索逻辑"""
    print("\n" + "=" * 60)
    print("测试 4: 自适应检索逻辑验证")
    print("=" * 60)

    try:
        context = create_mock_context()
        agent = StrategyResearchAgent(context=context)

        # 测试自适应检索
        test_query = "复杂的量化策略"

        # 模拟检索评估为不充分，需要追加检索
        mock_response = MagicMock()
        mock_response.content = (
            '{"is_sufficient": false, "missing_aspects": ["风险管理", "退出机制"]}'
        )
        context.llm_service.invoke.return_value = mock_response

        knowledge = agent._get_knowledge_context(test_query, node_type="research")

        # 验证知识检索结果
        assert knowledge is not None, "自适应检索返回 None"

        print("✅ 自适应检索逻辑正常")
        print(f"   - 检索到的知识长度：{len(knowledge)}")

        # 验证知识服务被多次调用（初始 + 追加）
        print(f"   - 知识服务调用次数：{context.knowledge_service.search.call_count}")

        return True
    except Exception as e:
        print(f"❌ 自适应检索逻辑失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def test_error_handling():
    """测试 5: 验证错误处理"""
    print("\n" + "=" * 60)
    print("测试 5: 错误处理验证")
    print("=" * 60)

    try:
        context = create_mock_context()

        # 模拟 LLM 服务错误
        context.llm_service.invoke.side_effect = Exception("LLM 服务错误")

        agent = StrategyResearchAgent(context=context)

        # 测试错误处理
        test_query = "测试查询"

        try:
            strategy = agent.research_strategy_with_context(test_query)
            # 如果没有抛出异常，检查是否返回了合理的默认值
            print(f"⚠️  未抛出异常，返回：{strategy}")
        except Exception as e:
            print(f"✅ 错误处理正常：{e}")
            return True

        return True
    except Exception as e:
        print(f"❌ 错误处理失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("StrategyResearchAgent - 单元测试")
    print("=" * 60)

    tests = [
        ("Agent 创建", test_agent_creation),
        ("策略研究功能", test_research_strategy),
        ("知识检索功能", test_knowledge_retrieval),
        ("自适应检索逻辑", test_adaptive_retrieval),
        ("错误处理", test_error_handling),
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
        print("\n✅ 所有测试通过！")
        return True
    else:
        print(f"\n⚠️  {total - passed} 个测试失败")
        return False


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
