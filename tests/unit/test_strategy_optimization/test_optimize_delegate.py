"""OptimizeDelegate 最小单测 — mock LLM 验证优化产出。"""

from __future__ import annotations

from unittest.mock import MagicMock

from long_earn.config import RuntimeContext
from long_earn.services import MemoryService
from long_earn.services.backtest_service import BacktestService
from long_earn.services.llm_service import LLMService
from long_earn.services.logger_service import LoggerService
from long_earn.services.monitoring_service import MonitoringService
from long_earn.services.stock_service import StockService
from long_earn.strategy_rd.optimize_delegate import OptimizeDelegate


def _make_mock_context() -> RuntimeContext:
    mock_llm = MagicMock(spec=LLMService)
    mock_response = MagicMock()
    mock_response.content = "optimized description"
    mock_llm.invoke.return_value = mock_response

    mock_memory = MagicMock(spec=MemoryService)
    mock_memory.search.return_value = []
    mock_memory.search_experience.return_value = []

    mock_config = MagicMock()
    mock_config.init_dir = "./init"

    return RuntimeContext(
        llm_service=mock_llm,
        memory=mock_memory,
        stock_service=MagicMock(spec=StockService),
        backtest_service=MagicMock(spec=BacktestService),
        logger=MagicMock(spec=LoggerService),
        monitoring=MagicMock(spec=MonitoringService),
        config=mock_config,
    )


def test_optimize_delegate_returns_optimized_true() -> None:
    context = _make_mock_context()
    delegate = OptimizeDelegate(context=context)

    result = delegate.optimize_strategy(
        strategy={"strategy_name": "TestStrategy", "description": "base"},
        improvement_suggestions=["提升 sharpe"],
        previous_backtest={"sharpe_ratio": 0.5, "total_return": 0.1},
    )

    assert result["optimized"] is True
    assert result["description"] == "optimized description"
    assert len(result["evolution_lineage"]) == 1
    context.llm_service.invoke.assert_called_once()
