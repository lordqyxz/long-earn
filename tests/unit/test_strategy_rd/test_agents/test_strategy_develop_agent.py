"""StrategyDevelopAgent 单元测试

测试策略开发 Agent 的核心功能，包括：
- develop_strategy() 方法
- refine_code() 方法
- 代码生成逻辑
- 错误修复逻辑
- Mock LLM 响应处理
"""

from unittest.mock import MagicMock

from long_earn.config import AppConfig, RuntimeContext
from long_earn.services.backtest_service import BacktestService
from long_earn.services.knowledge_service import KnowledgeService
from long_earn.services.llm_service import LLMService
from long_earn.services.logger_service import LoggerService
from long_earn.services.monitoring_service import MonitoringService
from long_earn.services.stock_service import StockService
from long_earn.strategy_rd.agents.strategy_develop_agent import StrategyDevelopAgent


def create_mock_context() -> RuntimeContext:
    """创建 Mock 运行时上下文"""
    # Mock LLM 服务
    mock_llm = MagicMock(spec=LLMService)
    mock_response = MagicMock()
    mock_response.content = """
class TestStrategy:
    '''测试策略类'''
    
    def __init__(self):
        self.stocks = ["600519", "000858"]
    
    def generate_signals(self, date):
        '''生成交易信号'''
        return {"600519": 0.5, "000858": 0.5}
"""
    mock_llm.invoke.return_value = mock_response

    # Mock 知识服务
    mock_knowledge = MagicMock(spec=KnowledgeService)
    mock_knowledge.search.return_value = ["代码实现示例 1", "代码实现示例 2"]
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
        agent = StrategyDevelopAgent(context=context)

        assert agent is not None, "Agent 创建失败"
        assert agent.context is not None, "Agent context 未设置"
        assert agent.llm_service is not None, "Agent llm_service 未设置"
        assert agent.knowledge_service is not None, "Agent knowledge_service 未设置"

        print("✅ StrategyDevelopAgent 创建成功")
        print(f"   - context: {type(agent.context).__name__}")
        print(f"   - llm_service: {type(agent.llm_service).__name__}")
        print(f"   - knowledge_service: {type(agent.knowledge_service).__name__}")

        return True
    except Exception as e:
        print(f"❌ Agent 创建失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def test_develop_strategy():
    """测试 2: 验证策略开发功能"""
    print("\n" + "=" * 60)
    print("测试 2: 策略开发功能验证")
    print("=" * 60)

    try:
        context = create_mock_context()
        agent = StrategyDevelopAgent(context=context)

        # 测试策略信息
        test_strategy = {
            "strategy_name": "测试动量策略",
            "description": "基于动量的策略",
            "logic": "当价格超过 20 日均线时买入",
            "entry_conditions": ["价格 > 20 日均线"],
            "exit_conditions": ["价格 < 20 日均线"],
            "risk_management": "止损 5%",
        }

        # 调用 develop_strategy
        code = agent.develop_strategy(test_strategy)

        # 验证返回结果
        assert code is not None, "策略开发返回 None"
        assert isinstance(code, str), "策略开发返回类型不正确"
        assert len(code) > 0, "策略开发返回空字符串"

        print("✅ 策略开发功能正常")
        print(f"   - 代码长度：{len(code)}")
        print(f"   - 代码预览：{code[:100]}...")

        # 验证 LLM 被调用
        assert context.llm_service.invoke.called, "LLM 服务未被调用"
        print(f"   - LLM 调用次数：{context.llm_service.invoke.call_count}")

        return True
    except Exception as e:
        print(f"❌ 策略开发功能失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def test_refine_code():
    """测试 3: 验证代码修复功能"""
    print("\n" + "=" * 60)
    print("测试 3: 代码修复功能验证")
    print("=" * 60)

    try:
        context = create_mock_context()
        agent = StrategyDevelopAgent(context=context)

        # 设置 Mock LLM 响应为修复后的代码
        mock_response = MagicMock()
        mock_response.content = """
class TestStrategy:
    '''修复后的测试策略类'''
    
    def __init__(self):
        self.stocks = ["600519", "000858", "002415"]
    
    def generate_signals(self, date):
        '''生成交易信号（修复版）'''
        import random
        num_stocks = random.randint(1, 2)
        selected = random.sample(self.stocks, num_stocks)
        weight = 1.0 / num_stocks
        return {stock: weight for stock in selected}
"""
        context.llm_service.invoke.return_value = mock_response

        # 测试策略和错误信息
        test_strategy = {
            "strategy_name": "测试动量策略",
            "description": "基于动量的策略",
        }

        error_message = "NameError: name 'random' is not defined"
        failed_code = """
class TestStrategy:
    def generate_signals(self, date):
        num_stocks = random.randint(1, 2)
        return {"600519": 1.0}
"""

        # 调用 refine_code
        refined_code = agent.refine_code(test_strategy, error_message, failed_code)

        # 验证返回结果
        assert refined_code is not None, "代码修复返回 None"
        assert isinstance(refined_code, str), "代码修复返回类型不正确"
        assert len(refined_code) > 0, "代码修复返回空字符串"

        print("✅ 代码修复功能正常")
        print(f"   - 修复后代码长度：{len(refined_code)}")
        print(f"   - 修复后代码预览：{refined_code[:100]}...")

        # 验证 LLM 被调用
        assert context.llm_service.invoke.called, "LLM 服务未被调用"
        print(f"   - LLM 调用次数：{context.llm_service.invoke.call_count}")

        return True
    except Exception as e:
        print(f"❌ 代码修复功能失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def test_knowledge_integration():
    """测试 4: 验证知识集成"""
    print("\n" + "=" * 60)
    print("测试 4: 知识集成验证")
    print("=" * 60)

    try:
        context = create_mock_context()
        agent = StrategyDevelopAgent(context=context)

        # 测试策略
        test_strategy = {
            "strategy_name": "均线策略",
            "description": "基于均线的策略",
        }

        # 调用 develop_strategy（会触发知识检索）
        code = agent.develop_strategy(test_strategy)

        # 验证知识服务被调用
        assert context.knowledge_service.search.called, "知识服务未被调用"
        print("✅ 知识集成正常")
        print(f"   - 知识服务调用次数：{context.knowledge_service.search.call_count}")

        return True
    except Exception as e:
        print(f"❌ 知识集成失败：{e}")
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

        agent = StrategyDevelopAgent(context=context)

        # 测试策略
        test_strategy = {
            "strategy_name": "测试策略",
            "description": "测试",
        }

        try:
            code = agent.develop_strategy(test_strategy)
            # 如果没有抛出异常，检查是否返回了合理的默认值
            print(f"⚠️  未抛出异常，返回：{code}")
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
    print("StrategyDevelopAgent - 单元测试")
    print("=" * 60)

    tests = [
        ("Agent 创建", test_agent_creation),
        ("策略开发功能", test_develop_strategy),
        ("代码修复功能", test_refine_code),
        ("知识集成", test_knowledge_integration),
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
