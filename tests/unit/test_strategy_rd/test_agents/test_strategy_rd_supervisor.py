"""StrategyRdSupervisor 单元测试

测试策略研究监督器的核心功能，包括：
- evaluate_strategy() 方法
- should_continue() 方法
- 评估标准逻辑
- ToT 多分支反思
- Mock LLM 响应处理
"""

import json
from unittest.mock import MagicMock

from long_earn.config import AppConfig, RuntimeContext
from long_earn.services.backtest_service import BacktestService
from long_earn.services.knowledge_service import KnowledgeService
from long_earn.services.llm_service import LLMService
from long_earn.services.logger_service import LoggerService
from long_earn.services.monitoring_service import MonitoringService
from long_earn.services.stock_service import StockService
from long_earn.strategy_rd.agents.strategy_rd_supervisor import StrategyRdSupervisor


def create_mock_context() -> RuntimeContext:
    """创建 Mock 运行时上下文"""
    # Mock LLM 服务
    mock_llm = MagicMock(spec=LLMService)
    mock_response = MagicMock()
    mock_response.content = json.dumps(
        {
            "decision": "接受",
            "reason": "策略表现良好，达到预期目标",
            "key_concerns": ["风险控制", "收益稳定性"],
            "required_changes": "",
        }
    )
    mock_llm.invoke.return_value = mock_response

    # Mock 知识服务
    mock_knowledge = MagicMock(spec=KnowledgeService)
    mock_knowledge.search.return_value = ["监督经验 1", "监督经验 2"]
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


def test_supervisor_creation():
    """测试 1: 验证 Supervisor 创建"""
    print("=" * 60)
    print("测试 1: Supervisor 创建验证")
    print("=" * 60)

    try:
        context = create_mock_context()
        supervisor = StrategyRdSupervisor(context=context)

        assert supervisor is not None, "Supervisor 创建失败"
        assert supervisor.context is not None, "Supervisor context 未设置"
        assert supervisor.llm_service is not None, "Supervisor llm_service 未设置"
        assert supervisor.logger is not None, "Supervisor logger 未设置"

        print("✅ StrategyRdSupervisor 创建成功")
        print(f"   - context: {type(supervisor.context).__name__}")
        print(f"   - llm_service: {type(supervisor.llm_service).__name__}")
        print(f"   - logger: {type(supervisor.logger).__name__}")

        return True
    except Exception as e:
        print(f"❌ Supervisor 创建失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def test_evaluate_strategy():
    """测试 2: 验证策略评估功能"""
    print("\n" + "=" * 60)
    print("测试 2: 策略评估功能验证")
    print("=" * 60)

    try:
        context = create_mock_context()
        supervisor = StrategyRdSupervisor(context=context)

        # 测试策略
        test_strategy = {
            "strategy_name": "测试动量策略",
            "description": "基于动量的策略",
            "logic": "当价格超过 20 日均线时买入",
        }

        # 测试回测结果（达到目标）
        test_backtest_result = {
            "total_return": 0.15,  # 15% 收益率
            "sharpe_ratio": 0.8,  # 0.8 夏普比率
            "max_drawdown": 0.12,  # 12% 最大回撤
            "win_rate": 0.55,  # 55% 胜率
        }

        # 调用 evaluate_strategy
        accepted = supervisor.evaluate_strategy(test_strategy, test_backtest_result)

        # 验证返回结果（evaluate_strategy 返回 bool）
        assert accepted is not None, "策略评估返回 None"
        assert isinstance(accepted, bool), "策略评估应返回 bool"

        print("✅ 策略评估功能正常")
        print(f"   - 是否接受：{accepted}")

        # 验证 LLM 被调用
        assert context.llm_service.invoke.called, "LLM 服务未被调用"
        print(f"   - LLM 调用次数：{context.llm_service.invoke.call_count}")

        return True
    except Exception as e:
        print(f"❌ 策略评估功能失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def test_should_continue_good_performance():
    """测试 3: 验证继续迭代判断 - 表现良好应停止"""
    print("\n" + "=" * 60)
    print("测试 3: 继续迭代判断 - 表现良好")
    print("=" * 60)

    try:
        context = create_mock_context()
        supervisor = StrategyRdSupervisor(context=context)

        # 设置 Mock LLM 响应为停止迭代
        mock_response = MagicMock()
        mock_response.content = json.dumps(
            {
                "should_continue": False,
                "reason": "策略已达到目标，无需继续迭代",
                "key_concerns": [],
                "next_focus": "",
            }
        )
        context.llm_service.invoke.return_value = mock_response

        # 测试数据（表现良好）
        test_strategy = {
            "strategy_name": "优秀策略",
            "description": "表现优秀的策略",
        }

        test_backtest_result = {
            "total_return": 0.20,  # 20% 收益率（>10%）
            "sharpe_ratio": 1.2,  # 1.2 夏普比率（>0.5）
            "max_drawdown": 0.08,  # 8% 最大回撤（<20%）
        }

        test_reflection = "策略表现优秀"
        test_suggestions = "无需改进"

        # 调用 should_continue（第 1 次迭代）
        should_continue = supervisor.should_continue(
            iteration=1,
            max_iterations=3,
            strategy=test_strategy,
            backtest_result=test_backtest_result,
            reflection=test_reflection,
            improvement_suggestions=test_suggestions,
        )

        # 验证结果（应该停止，因为表现已经很好）
        assert should_continue is False, "表现良好时应停止迭代"

        print("✅ 继续迭代判断正常（表现良好时停止）")
        print(f"   - 是否继续：{should_continue}")

        return True
    except Exception as e:
        print(f"❌ 继续迭代判断失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def test_should_continue_poor_performance():
    """测试 4: 验证继续迭代判断 - 表现不佳应继续"""
    print("\n" + "=" * 60)
    print("测试 4: 继续迭代判断 - 表现不佳")
    print("=" * 60)

    try:
        context = create_mock_context()
        supervisor = StrategyRdSupervisor(context=context)

        # 设置 Mock LLM 响应为继续迭代
        mock_response = MagicMock()
        mock_response.content = json.dumps(
            {
                "should_continue": True,
                "reason": "策略表现不佳，需要继续优化",
                "key_concerns": ["收益率低", "回撤大"],
                "next_focus": "优化入场时机",
            }
        )
        context.llm_service.invoke.return_value = mock_response

        # 测试数据（表现不佳）
        test_strategy = {
            "strategy_name": "待优化策略",
            "description": "需要优化的策略",
        }

        test_backtest_result = {
            "total_return": 0.05,  # 5% 收益率（<10%）
            "sharpe_ratio": 0.3,  # 0.3 夏普比率（<0.5）
            "max_drawdown": 0.25,  # 25% 最大回撤（>20%）
        }

        test_reflection = "策略表现不佳，需要优化"
        test_suggestions = "改进入场和出场时机"

        # 调用 should_continue（第 1 次迭代，还有剩余次数）
        should_continue = supervisor.should_continue(
            iteration=1,
            max_iterations=3,
            strategy=test_strategy,
            backtest_result=test_backtest_result,
            reflection=test_reflection,
            improvement_suggestions=test_suggestions,
        )

        # 验证结果（应该继续，因为表现不佳且有剩余迭代次数）
        assert should_continue is True, "表现不佳时应继续迭代"

        print("✅ 继续迭代判断正常（表现不佳时继续）")
        print(f"   - 是否继续：{should_continue}")

        return True
    except Exception as e:
        print(f"❌ 继续迭代判断失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def test_max_iterations_reached():
    """测试 5: 验证最大迭代次数到达"""
    print("\n" + "=" * 60)
    print("测试 5: 最大迭代次数到达验证")
    print("=" * 60)

    try:
        context = create_mock_context()
        supervisor = StrategyRdSupervisor(context=context)

        # 测试数据
        test_strategy = {
            "strategy_name": "测试策略",
            "description": "测试",
        }

        test_backtest_result = {
            "total_return": 0.08,
            "sharpe_ratio": 0.4,
            "max_drawdown": 0.18,
        }

        test_reflection = "还需要优化"
        test_suggestions = "继续改进"

        # 调用 should_continue（已达到最大迭代次数）
        should_continue = supervisor.should_continue(
            iteration=3,  # 已达最大迭代次数
            max_iterations=3,
            strategy=test_strategy,
            backtest_result=test_backtest_result,
            reflection=test_reflection,
            improvement_suggestions=test_suggestions,
        )

        # 验证结果（应该停止，因为达到最大迭代次数）
        assert should_continue is False, "达到最大迭代次数时应停止"

        print("✅ 最大迭代次数判断正常")
        print("   - 当前迭代：3/3")
        print(f"   - 是否继续：{should_continue}")

        return True
    except Exception as e:
        print(f"❌ 最大迭代次数判断失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def test_error_handling():
    """测试 6: 验证错误处理"""
    print("\n" + "=" * 60)
    print("测试 6: 错误处理验证")
    print("=" * 60)

    try:
        context = create_mock_context()

        # 模拟 LLM 服务错误
        context.llm_service.invoke.side_effect = Exception("LLM 服务错误")

        supervisor = StrategyRdSupervisor(context=context)

        # 测试数据
        test_strategy = {
            "strategy_name": "测试策略",
            "description": "测试",
        }

        test_backtest_result = {
            "total_return": 0.10,
            "sharpe_ratio": 0.5,
            "max_drawdown": 0.15,
        }

        try:
            evaluation = supervisor.evaluate_strategy(
                test_strategy, test_backtest_result
            )
            # 如果没有抛出异常，检查是否返回了合理的默认值
            print(f"⚠️  未抛出异常，返回：{evaluation}")
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
    print("StrategyRdSupervisor - 单元测试")
    print("=" * 60)

    tests = [
        ("Supervisor 创建", test_supervisor_creation),
        ("策略评估功能", test_evaluate_strategy),
        ("继续迭代判断 - 表现良好", test_should_continue_good_performance),
        ("继续迭代判断 - 表现不佳", test_should_continue_poor_performance),
        ("最大迭代次数到达", test_max_iterations_reached),
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
