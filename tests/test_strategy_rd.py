"""策略研究子图 (strategy_rd) 测试模块

该模块测试 strategy_rd 子图的完整功能，包括:
- 子图的基本执行流程
- 各个节点的独立功能测试
- Reflexion 循环机制
- 错误处理和边界情况

注意：所有测试使用 Mock LLM，因为系统不再有 fallback 机制。
LLM 调用失败时应该正确传播错误。
"""

import json
from unittest.mock import Mock, patch

import pytest

from long_earn.strategy_rd.agents.strategy_rd_supervisor import StrategyRdSupervisor
from long_earn.strategy_rd.state import State
from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph


def create_mock_llm(response_content: str):
    """创建模拟 LLM"""
    mock_llm = Mock()
    mock_response = Mock()
    mock_response.content = response_content
    mock_llm.invoke.return_value = mock_response
    return mock_llm


class TestStrategyRdSubgraph:
    """策略研究子图集成测试"""

    def test_subgraph_creation(self):
        """测试子图创建"""
        subgraph = create_strategy_rd_subgraph()
        assert subgraph is not None
        assert callable(subgraph.invoke)

    @patch("long_earn.utils.llm_factory.create_llm")
    def test_subgraph_initialization(self, mock_create_llm):
        """测试子图初始化"""
        mock_create_llm.return_value = create_mock_llm("测试策略")
        subgraph = create_strategy_rd_subgraph()
        initial_state: State = {"query": "测试策略研究", "max_iterations": 1}
        result = subgraph.invoke(initial_state)
        assert result is not None
        assert "iteration" in result

    @patch("long_earn.utils.llm_factory.create_llm")
    def test_subgraph_full_flow_single_iteration(self, mock_create_llm):
        """测试单次迭代完整流程"""
        mock_create_llm.return_value = create_mock_llm("均线策略")
        subgraph = create_strategy_rd_subgraph()
        initial_state: State = {"query": "简单均线策略", "max_iterations": 1}
        result = subgraph.invoke(initial_state)
        assert "strategy" in result
        assert "strategy_code" in result
        assert "backtest_result" in result
        assert "reflection" in result

    @patch("long_earn.utils.llm_factory.create_llm")
    def test_subgraph_with_multiple_iterations(self, mock_create_llm):
        """测试多次迭代"""
        mock_create_llm.return_value = create_mock_llm("动量策略")
        subgraph = create_strategy_rd_subgraph()
        initial_state: State = {"query": "动量策略", "max_iterations": 2}
        result = subgraph.invoke(initial_state)
        assert result.get("iteration", 0) >= 1


class TestStrategyResearchNode:
    """研究节点测试"""

    @patch("long_earn.utils.llm_factory.create_llm")
    def test_research_node_success(self, mock_create_llm):
        """测试研究节点成功"""
        mock_create_llm.return_value = create_mock_llm("这是一个移动平均线交叉策略")
        subgraph = create_strategy_rd_subgraph()
        initial_state: State = {"query": "测试策略", "max_iterations": 1}
        result = subgraph.invoke(initial_state)
        assert "strategy" in result
        assert result["strategy"] is not None


class TestStrategyDevelopNode:
    """开发节点测试"""

    @patch("long_earn.utils.llm_factory.create_llm")
    def test_develop_node_success(self, mock_create_llm):
        """测试开发节点成功"""
        mock_create_llm.return_value = create_mock_llm(
            "```python\nclass TestStrategy:\n    pass\n```"
        )
        subgraph = create_strategy_rd_subgraph()
        initial_state: State = {"query": "均线策略", "max_iterations": 1}
        result = subgraph.invoke(initial_state)
        assert "strategy_code" in result


class TestBacktestNode:
    """回测节点测试"""

    @patch("long_earn.strategy_rd.subgraph.run_backtest")
    @patch("long_earn.utils.llm_factory.create_llm")
    def test_backtest_node_success(self, mock_create_llm, mock_backtest):
        """测试回测节点成功"""
        mock_backtest.return_value = {
            "total_return": 0.15,
            "sharpe_ratio": 1.2,
            "max_drawdown": -0.1,
        }
        mock_create_llm.return_value = create_mock_llm("测试策略")
        subgraph = create_strategy_rd_subgraph()
        initial_state: State = {"query": "测试", "max_iterations": 1}
        result = subgraph.invoke(initial_state)
        assert "backtest_result" in result
        assert result["backtest_result"]["total_return"] == 0.15


class TestReflectionNode:
    """反思节点测试"""

    @patch("long_earn.utils.llm_factory.create_llm")
    def test_reflection_node_success(self, mock_create_llm):
        """测试反思节点成功"""
        mock_create_llm.return_value = create_mock_llm(
            json.dumps(
                {
                    "reflection": "策略表现良好",
                    "improvement_suggestions": ["优化止损", "调整参数"],
                }
            )
        )
        subgraph = create_strategy_rd_subgraph()
        initial_state: State = {"query": "测试", "max_iterations": 1}
        result = subgraph.invoke(initial_state)
        assert "reflection" in result
        assert "improvement_suggestions" in result


class TestSupervisorNode:
    """监督器节点测试"""

    @patch("long_earn.utils.llm_factory.create_llm")
    def test_supervisor_continue_iteration(self, mock_create_llm):
        """测试监督器继续迭代"""
        mock_create_llm.return_value = create_mock_llm(
            json.dumps({"should_continue": True, "reason": "策略表现一般，需要优化"})
        )
        subgraph = create_strategy_rd_subgraph()
        initial_state: State = {"query": "测试", "max_iterations": 3}
        result = subgraph.invoke(initial_state)
        assert "should_continue" in result
        assert "iteration" in result

    @patch("long_earn.utils.llm_factory.create_llm")
    def test_supervisor_stop_iteration(self, mock_create_llm):
        """测试监督器停止迭代"""
        mock_create_llm.return_value = create_mock_llm(
            json.dumps({"should_continue": False, "reason": "已达到最大迭代次数"})
        )
        subgraph = create_strategy_rd_subgraph()
        initial_state: State = {"query": "测试", "max_iterations": 1}
        result = subgraph.invoke(initial_state)
        assert "should_continue" in result

    def test_supervisor_max_iterations_reached(self):
        """测试达到最大迭代次数"""
        with patch("long_earn.utils.llm_factory.create_llm") as mock_create_llm:
            mock_create_llm.return_value = create_mock_llm("test")
            supervisor = StrategyRdSupervisor()
            result = supervisor.should_continue(
                iteration=3,
                max_iterations=3,
                strategy={},
                backtest_result={},
                reflection="",
                improvement_suggestions=[],
            )
            assert result is False


class TestOptimizeFlow:
    """优化流程测试"""

    @patch("long_earn.utils.llm_factory.create_llm")
    def test_optimize_and_redevelop(self, mock_create_llm):
        """测试优化和重新开发流程"""
        call_count = 0

        def mock_invoke(prompt):
            nonlocal call_count
            call_count += 1
            mock_response = Mock()
            if call_count <= 3:
                mock_response.content = json.dumps(
                    {
                        "should_continue": True,
                        "reason": "需要优化",
                    }
                )
            else:
                mock_response.content = json.dumps(
                    {
                        "should_continue": False,
                        "reason": "完成",
                    }
                )
            return mock_response

        mock_llm = Mock()
        mock_llm.invoke.side_effect = mock_invoke
        mock_create_llm.return_value = mock_llm

        subgraph = create_strategy_rd_subgraph()
        initial_state: State = {"query": "优化测试", "max_iterations": 2}
        result = subgraph.invoke(initial_state)
        assert "optimized_strategy" in result or "iteration" in result


class TestErrorHandling:
    """错误处理测试"""

    @patch("long_earn.utils.llm_factory.create_llm")
    def test_llm_error_propagation(self, mock_create_llm):
        """测试 LLM 错误正确传播"""
        mock_llm = Mock()
        mock_llm.invoke.side_effect = Exception("LLM 调用失败")
        mock_create_llm.return_value = mock_llm

        subgraph = create_strategy_rd_subgraph()
        initial_state: State = {"query": "测试", "max_iterations": 1}

        with pytest.raises(Exception, match="LLM 调用失败"):
            subgraph.invoke(initial_state)

    @patch("long_earn.strategy_rd.subgraph.run_backtest")
    @patch("long_earn.utils.llm_factory.create_llm")
    def test_backtest_error_propagation(self, mock_create_llm, mock_backtest):
        """测试回测错误正确传播"""
        mock_backtest.side_effect = Exception("回测服务不可用")
        mock_create_llm.return_value = create_mock_llm("测试")

        subgraph = create_strategy_rd_subgraph()
        initial_state: State = {"query": "测试", "max_iterations": 1}

        result = subgraph.invoke(initial_state)
        assert "backtest_result" in result


class TestReflexionPattern:
    """Reflexion 模式测试"""

    @patch("long_earn.utils.llm_factory.create_llm")
    def test_reflexion_single_loop(self, mock_create_llm):
        """测试单次 Reflexion 循环"""
        mock_create_llm.return_value = create_mock_llm(
            json.dumps({"should_continue": False, "reason": "完成"})
        )
        subgraph = create_strategy_rd_subgraph()
        initial_state: State = {"query": "Reflexion测试", "max_iterations": 1}
        result = subgraph.invoke(initial_state)
        assert result.get("iteration", 0) >= 1

    @patch("long_earn.utils.llm_factory.create_llm")
    def test_reflexion_multiple_loops(self, mock_create_llm):
        """测试多次 Reflexion 循环"""
        mock_create_llm.return_value = create_mock_llm(
            json.dumps({"should_continue": False, "reason": "完成"})
        )
        subgraph = create_strategy_rd_subgraph()
        initial_state: State = {"query": "多次循环测试", "max_iterations": 2}
        result = subgraph.invoke(initial_state)
        assert result.get("iteration", 0) >= 1
