"""Unit tests for strategy_rd subgraph following pytest best practices"""

from unittest.mock import Mock, patch

import pytest


class TestStrategyResearchAgent:
    """Tests for StrategyResearchAgent"""

    @pytest.fixture
    def agent(self):
        """创建策略研究智能体实例"""
        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        return StrategyResearchAgent(
            llm_type="ollama",
            model_name="test-model",
            base_url="http://localhost:11434",
        )

    @pytest.fixture
    def mock_llm(self):
        """提供mock LLM"""
        mock_llm = Mock()
        mock_response = Mock()
        mock_response.content = '{"strategy": "测试策略", "parameters": {}}'
        mock_llm.invoke.return_value = mock_response
        return mock_llm

    def test_research_strategy_success(self, agent, mock_llm):
        """测试成功研究策略"""
        with patch.object(agent, "_create_llm", return_value=mock_llm):
            result = agent.research_strategy("测试策略研究")

        assert "strategy_name" in result
        assert "description" in result
        assert result["query"] == "测试策略研究"

    def test_research_strategy_failure(self, agent):
        """测试LLM失败时的降级处理"""
        with patch.object(agent, "_create_llm") as mock_create_llm:
            mock_llm = Mock()
            mock_llm.invoke.side_effect = Exception("LLM Error")
            mock_create_llm.return_value = mock_llm

            result = agent.research_strategy("测试策略研究")

        assert "strategy_name" in result
        assert "description" in result

    def test_reflect_success(self, agent, mock_llm):
        """测试成功反思策略"""
        mock_response = Mock()
        mock_response.content = (
            '{"reflection": "策略表现良好", "improvement_suggestions": ["优化参数"]}'
        )
        mock_llm.invoke.return_value = mock_response

        with patch.object(agent, "_create_llm", return_value=mock_llm):
            result = agent.reflect(
                strategy={"name": "测试策略"},
                backtest_result={"total_return": 0.1},
            )

        assert "reflection" in result
        assert "improvement_suggestions" in result

    def test_reflect_failure(self, agent):
        """测试反思失败时的降级处理"""
        with patch.object(agent, "_create_llm") as mock_create_llm:
            mock_llm = Mock()
            mock_llm.invoke.side_effect = Exception("LLM Error")
            mock_create_llm.return_value = mock_llm

            result = agent.reflect(
                strategy={"name": "测试策略"},
                backtest_result={"total_return": 0.1},
            )

        assert "reflection" in result
        assert "improvement_suggestions" in result

    def test_optimize_strategy_success(self, agent, mock_llm):
        """测试成功优化策略"""
        mock_response = Mock()
        mock_response.content = "优化后的策略描述"
        mock_llm.invoke.return_value = mock_response

        with patch.object(agent, "_create_llm", return_value=mock_llm):
            result = agent.optimize_strategy(
                strategy={"name": "测试策略", "description": "原始描述"},
                improvement_suggestions=["调整参数"],
            )

        assert "description" in result
        assert result.get("optimized") is True

    def test_optimize_strategy_failure(self, agent):
        """测试优化失败时的降级处理"""
        with patch.object(agent, "_create_llm") as mock_create_llm:
            mock_llm = Mock()
            mock_llm.invoke.side_effect = Exception("LLM Error")
            mock_create_llm.return_value = mock_llm

            result = agent.optimize_strategy(
                strategy={"name": "测试策略"},
                improvement_suggestions=["调整参数"],
            )

        assert result.get("optimized") is True


class TestStrategyDevelopAgent:
    """Tests for StrategyDevelopAgent"""

    @pytest.fixture
    def agent(self):
        """创建策略开发智能体实例"""
        from long_earn.strategy_rd.agents.strategy_develop_agent import (
            StrategyDevelopAgent,
        )

        return StrategyDevelopAgent(
            llm_type="ollama",
            model_name="test-model",
            base_url="http://localhost:11434",
        )

    @pytest.fixture
    def mock_llm(self):
        """提供mock LLM"""
        mock_llm = Mock()
        mock_response = Mock()
        mock_response.content = "```python\nclass TestStrategy:\n    pass\n```"
        mock_llm.invoke.return_value = mock_response
        return mock_llm

    def test_develop_strategy_success(self, agent, mock_llm):
        """测试成功开发策略"""
        with patch.object(agent, "_create_llm", return_value=mock_llm):
            result = agent.develop_strategy(
                strategy={
                    "description": "测试策略描述",
                    "strategy_name": "TestStrategy",
                }
            )

        assert isinstance(result, str)
        assert len(result) > 0

    def test_develop_strategy_with_code_block(self, agent, mock_llm):
        """测试从代码块中提取策略代码"""
        mock_response = Mock()
        mock_response.content = "```python\nclass CustomStrategy:\n    def __init__(self):\n        pass\n```"
        mock_llm.invoke.return_value = mock_response

        with patch.object(agent, "_create_llm", return_value=mock_llm):
            result = agent.develop_strategy(strategy={"description": "测试"})

        assert "class CustomStrategy" in result

    def test_develop_strategy_failure(self, agent):
        """测试开发失败时的降级处理"""
        with patch.object(agent, "_create_llm") as mock_create_llm:
            mock_llm = Mock()
            mock_llm.invoke.side_effect = Exception("LLM Error")
            mock_create_llm.return_value = mock_llm

            result = agent.develop_strategy(strategy={"description": "测试策略"})

        assert isinstance(result, str)


class TestStrategyRdSupervisor:
    """Tests for StrategyRdSupervisor"""

    @pytest.fixture
    def supervisor(self):
        """创建监督器实例"""
        from long_earn.strategy_rd.agents.strategy_rd_supervisor import (
            StrategyRdSupervisor,
        )

        return StrategyRdSupervisor(
            llm_type="ollama",
            model_name="test-model",
            base_url="http://localhost:11434",
        )

    @pytest.fixture
    def mock_llm(self):
        """提供mock LLM"""
        mock_llm = Mock()
        return mock_llm

    def test_should_continue_max_iterations_reached(self, supervisor):
        """测试达到最大迭代次数时停止"""
        result = supervisor.should_continue(
            iteration=3,
            max_iterations=3,
            strategy={},
            backtest_result={},
            reflection="",
            improvement_suggestions=[],
        )

        assert result is False

    def test_should_continue_within_limit(self, supervisor, mock_llm):
        """测试在迭代限制内继续"""
        mock_response = Mock()
        mock_response.content = '{"should_continue": true, "reason": "策略需要优化"}'
        mock_llm.invoke.return_value = mock_response

        with patch.object(supervisor, "_create_llm", return_value=mock_llm):
            result = supervisor.should_continue(
                iteration=1,
                max_iterations=3,
                strategy={"name": "测试策略"},
                backtest_result={"total_return": 0.05},
                reflection="策略表现一般",
                improvement_suggestions=["增加仓位"],
            )

        assert isinstance(result, bool)

    def test_should_continue_failure(self, supervisor):
        """测试判断失败时的降级处理"""
        with patch.object(supervisor, "_create_llm") as mock_create_llm:
            mock_llm = Mock()
            mock_llm.invoke.side_effect = Exception("LLM Error")
            mock_create_llm.return_value = mock_llm

            result = supervisor.should_continue(
                iteration=1,
                max_iterations=3,
                strategy={},
                backtest_result={},
                reflection="",
                improvement_suggestions=[],
            )

        assert result is True

    def test_evaluate_strategy_success(self, supervisor, mock_llm):
        """测试成功评估策略"""
        mock_response = Mock()
        mock_response.content = '{"decision": "接受", "reason": "策略表现良好"}'
        mock_llm.invoke.return_value = mock_response

        with patch.object(supervisor, "_create_llm", return_value=mock_llm):
            result = supervisor.evaluate_strategy(
                strategy={"name": "测试策略"},
                backtest_result={"total_return": 0.15, "sharpe_ratio": 1.2},
            )

        assert isinstance(result, bool)

    def test_evaluate_strategy_failure(self, supervisor):
        """测试评估失败时的降级处理"""
        with patch.object(supervisor, "_create_llm") as mock_create_llm:
            mock_llm = Mock()
            mock_llm.invoke.side_effect = Exception("LLM Error")
            mock_create_llm.return_value = mock_llm

            result = supervisor.evaluate_strategy(
                strategy={},
                backtest_result={},
            )

        assert result is False


class TestStrategyRdSubgraph:
    """Tests for strategy_rd subgraph integration"""

    def test_create_subgraph(self):
        """测试创建子图"""
        from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph

        subgraph = create_strategy_rd_subgraph()

        assert subgraph is not None
        assert hasattr(subgraph, "invoke")

    def test_subgraph_has_required_nodes(self):
        """测试子图包含必需的节点"""
        from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph

        subgraph = create_strategy_rd_subgraph()

        assert subgraph is not None


class TestStrategyRdState:
    """Tests for strategy_rd state definition"""

    def test_state_fields(self):
        """测试状态字段定义"""
        from long_earn.strategy_rd.state import State

        state: State = {
            "query": "测试",
            "iteration": 0,
            "max_iterations": 3,
            "strategy": {},
            "strategy_code": "",
            "backtest_result": {},
            "reflection": "",
            "improvement_suggestions": "",
            "optimized_strategy": {},
            "optimized_strategy_code": "",
            "should_continue": False,
        }

        assert state.get("query") == "测试"
        assert state.get("iteration") == 0
        assert state.get("max_iterations") == 3
