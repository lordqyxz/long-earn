"""Unit tests for strategy_research subgraph following pytest best practices"""
import pytest
from unittest.mock import Mock, patch, MagicMock, mock_open

from long_earn.strategy_research.nodes import (
    strategy_generate_agent,
    generate_strategy_node,
    run_backtest_node,
    strategy_reflection_node,
    evaluate_strategy_node
)


class TestStrategyGenerateAgent:
    """Tests for strategy_generate_agent node"""
    
    @pytest.fixture
    def base_state(self):
        """提供基础状态"""
        return {
            'target_market': 'stock',
            'output_dir': 'strategies',
            'llm_type': 'ollama',
            'model_name': 'test-model',
            'base_url': 'http://localhost:11434'
        }
    
    @pytest.fixture
    def mock_llm(self):
        """提供mock LLM"""
        mock_llm = Mock()
        mock_response = Mock()
        mock_response.content = "Test strategy idea"
        mock_llm.invoke.return_value = mock_response
        return mock_llm
    
    def test_strategy_generate_agent_success(self, base_state, mock_llm):
        """测试成功生成策略"""
        with patch('long_earn.utils.llm_factory.create_llm', return_value=mock_llm):
            result = strategy_generate_agent(base_state)
        
        assert 'strategy_idea' in result
        assert result['strategy_idea'] == "Test strategy idea"
        mock_llm.invoke.assert_called_once()
    
    def test_strategy_generate_agent_with_improvement(self, mock_llm):
        """测试使用改进建议生成策略"""
        state = {
            'target_market': 'stock',
            'output_dir': 'strategies',
            'improvement_suggestions': 'Increase position size',
            'llm_type': 'ollama',
            'model_name': 'test-model',
            'base_url': 'http://localhost:11434'
        }
        
        with patch('long_earn.utils.llm_factory.create_llm', return_value=mock_llm):
            result = strategy_generate_agent(state)
        
        assert result['strategy_idea'] == "Test strategy idea"
    
    def test_strategy_generate_agent_failure(self, base_state):
        """测试LLM失败时的处理"""
        with patch('long_earn.utils.llm_factory.create_llm') as mock_create_llm:
            mock_llm = Mock()
            mock_llm.invoke.side_effect = Exception("LLM Error")
            mock_create_llm.return_value = mock_llm
            
            result = strategy_generate_agent(base_state)
        
        assert '默认策略思路' in str(result['strategy_idea'])


class TestGenerateStrategyNode:
    """Tests for generate_strategy_node"""
    
    def test_generate_strategy_node_success(self):
        """测试成功生成策略节点"""
        with patch('long_earn.agent.strategy_generator.generate_strategy') as mock_gen:
            with patch('long_earn.memory.vector_store.VectorStore') as mock_vs:
                with patch('long_earn.memory.db.StrategyDatabase') as mock_db:
                    mock_gen.return_value = '/tmp/test_strategy.py'
                    
                    with patch('builtins.open', mock_open(read_data='test code')):
                        state = {
                            'target_market': 'stock',
                            'output_dir': 'strategies',
                            'strategy_idea': 'Test strategy',
                            'llm_type': 'ollama',
                            'model_name': 'test-model',
                            'base_url': 'http://localhost:11434'
                        }
                        
                        result = generate_strategy_node(state)
                        
                        assert 'strategy_path' in result
                        assert result['strategy_path'] == '/tmp/test_strategy.py'
                        assert result['strategy_idea'] == 'Test strategy'
                        mock_gen.assert_called_once()


class TestRunBacktestNode:
    """Tests for run_backtest_node"""
    
    def test_run_backtest_success(self):
        """测试成功执行回测"""
        expected_results = {
            '总收益率': '10.5%',
            '夏普比率': '1.5',
            '最大回撤': '5.2%'
        }
        
        with patch('long_earn.qlib.backtest.run_backtest', return_value=expected_results):
            state = {
                'target_market': 'stock',
                'output_dir': 'strategies',
                'strategy_path': '/tmp/test_strategy.py',
                'start_date': '2020-01-01',
                'end_date': '2023-12-31',
                'llm_type': 'ollama',
                'model_name': 'test-model',
                'base_url': 'http://localhost:11434'
            }
            
            result = run_backtest_node(state)
            
            assert 'backtest_results' in result
            assert result['backtest_results'] == expected_results
    
    def test_run_backtest_no_strategy(self):
        """测试没有策略路径时的处理"""
        with patch('long_earn.qlib.backtest.run_backtest') as mock_run:
            state = {
                'target_market': 'stock',
                'output_dir': 'strategies',
                'strategy_path': None,
                'start_date': '2020-01-01',
                'end_date': '2023-12-31',
                'llm_type': 'ollama',
                'model_name': 'test-model',
                'base_url': 'http://localhost:11434'
            }
            
            result = run_backtest_node(state)
            
            assert result['backtest_results'] is None
            mock_run.assert_not_called()


class TestStrategyReflectionNode:
    """Tests for strategy_reflection_node"""
    
    def test_strategy_reflection_success(self):
        """测试成功反思策略"""
        mock_llm = Mock()
        mock_response = Mock()
        mock_response.content = "Strategy needs improvement"
        mock_llm.invoke.return_value = mock_response
        
        with patch('long_earn.utils.llm_factory.create_llm', return_value=mock_llm):
            state = {
                'target_market': 'stock',
                'output_dir': 'strategies',
                'strategy_path': '/tmp/test.py',
                'strategy_idea': 'Test strategy',
                'backtest_results': {'总收益率': '5%'},
                'llm_type': 'ollama',
                'model_name': 'test-model',
                'base_url': 'http://localhost:11434'
            }
            
            result = strategy_reflection_node(state)
            
            assert 'reflection' in result
            assert 'improvement_suggestions' in result
            assert result['reflection'] == "Strategy needs improvement"
    
    def test_strategy_reflection_failure(self):
        """测试反思失败时的处理"""
        with patch('long_earn.utils.llm_factory.create_llm') as mock_create_llm:
            mock_llm = Mock()
            mock_llm.invoke.side_effect = Exception("Reflection failed")
            mock_create_llm.return_value = mock_llm
            
            state = {
                'target_market': 'stock',
                'output_dir': 'strategies',
                'strategy_path': '/tmp/test.py',
                'strategy_idea': 'Test strategy',
                'backtest_results': {'总收益率': '5%'},
                'llm_type': 'ollama',
                'model_name': 'test-model',
                'base_url': 'http://localhost:11434'
            }
            
            result = strategy_reflection_node(state)
        
        assert result['reflection'] == "策略反思失败"


class TestEvaluateStrategyNode:
    """Tests for evaluate_strategy_node"""
    
    @pytest.mark.parametrize("return_value,expected_next_step", [
        ('10%', 'end'),
        ('5.5%', 'end'),
        ('-5%', 'improve'),
        ('0%', 'improve'),
    ])
    def test_evaluate_strategy_parametrized(self, return_value, expected_next_step):
        """测试参数化的评估场景"""
        state = {
            'target_market': 'stock',
            'output_dir': 'strategies',
            'strategy_path': '/tmp/test.py',
            'strategy_idea': 'Test strategy',
            'backtest_results': {'总收益率': return_value},
            'llm_type': 'ollama',
            'model_name': 'test-model',
            'base_url': 'http://localhost:11434'
        }
        
        result = evaluate_strategy_node(state)
        
        assert result['next_step'] == expected_next_step
    
    def test_evaluate_strategy_no_backtest_results(self):
        """测试没有回测结果时的处理"""
        state = {
            'target_market': 'stock',
            'output_dir': 'strategies',
            'strategy_path': '/tmp/test.py',
            'strategy_idea': 'Test strategy',
            'backtest_results': None,
            'llm_type': 'ollama',
            'model_name': 'test-model',
            'base_url': 'http://localhost:11434'
        }
        
        result = evaluate_strategy_node(state)
        
        assert result['next_step'] == 'improve'
    
    def test_evaluate_strategy_with_positive_return(self):
        """测试正收益率的评估"""
        state = {
            'target_market': 'stock',
            'output_dir': 'strategies',
            'strategy_path': '/tmp/test.py',
            'strategy_idea': 'Test strategy',
            'backtest_results': {'总收益率': '10%'},
            'llm_type': 'ollama',
            'model_name': 'test-model',
            'base_url': 'http://localhost:11434'
        }
        
        result = evaluate_strategy_node(state)
        
        assert result['next_step'] == 'end'
        assert 'strategy_path' in result
