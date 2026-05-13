"""策略研发子图接口测试"""

import json
from unittest.mock import MagicMock

from long_earn.config import RuntimeContext
from long_earn.services import MemoryService
from long_earn.services.backtest_service import BacktestService
from long_earn.services.llm_service import LLMService
from long_earn.services.logger_service import LoggerService
from long_earn.services.monitoring_service import MonitoringService
from long_earn.services.stock_service import StockService


def _make_mock_context() -> RuntimeContext:
    """创建带 mock 服务的 RuntimeContext"""
    mock_llm = MagicMock(spec=LLMService)
    mock_response = MagicMock()
    mock_response.content = "test response"
    mock_llm.invoke.return_value = mock_response

    mock_memory = MagicMock(spec=MemoryService)
    mock_memory.search.return_value = ["test knowledge"]
    mock_memory.recall.return_value = [
        {"content": "test", "metadata": {}, "similarity": 0.9}
    ]
    mock_memory.save_experience.return_value = True

    mock_config = MagicMock()
    mock_config.llm_type = "ollama"
    mock_config.llm_model = "test"
    mock_config.llm_base_url = "http://localhost"
    mock_config.memory_path = "~/.long_earn/memory.npz"
    mock_config.init_dir = "./init"
    mock_config.max_iterations = 1
    mock_config.backtest_start_date = "2020-01-01"
    mock_config.backtest_end_date = "2023-12-31"

    return RuntimeContext(
        llm_service=mock_llm,
        memory=mock_memory,
        stock_service=MagicMock(spec=StockService),
        backtest_service=MagicMock(spec=BacktestService),
        logger=MagicMock(spec=LoggerService),
        monitoring=MagicMock(spec=MonitoringService),
        config=mock_config,
    )


class TestPromptModuleImports:
    def test_create_strategy_research_prompt_returns_string(self):
        from long_earn.strategy_rd.agents.strategy_research_prompt import (
            create_strategy_research_prompt,
        )

        result = create_strategy_research_prompt(
            target_market="stock",
            query="test query",
            strategy_examples="none",
            strategy_context="none",
        )
        assert isinstance(result, str)
        assert len(result) > 0


class TestBranchReflection:
    def test_run_branch_reflection_calls_llm_service(self):
        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        context = _make_mock_context()
        agent = StrategyResearchAgent(context=context)

        branch_result = {
            "direction": "收益增强",
            "reflection": "Returns are low",
            "improvement_suggestions": [
                {"priority": "high", "issue": "low return", "suggestion": "add factors"}
            ],
        }
        mock_response = MagicMock()
        mock_response.content = json.dumps(branch_result)
        context.llm_service.invoke.return_value = mock_response

        result = agent._run_branch_reflection(
            direction="收益增强",
            strategy={"description": "test strategy"},
            backtest_result={"metrics": {"return": 5}},
        )

        assert context.llm_service.invoke.called
        assert result["direction"] == "收益增强"
        assert "reflection" in result


class TestGraphStructure:
    def test_subgraph_compiles(self):
        from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph

        context = _make_mock_context()
        subgraph = create_strategy_rd_subgraph(context)
        assert subgraph is not None
