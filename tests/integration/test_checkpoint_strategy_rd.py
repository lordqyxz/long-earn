"""利用 checkpoint 机制测试策略研发功能准确性

验证 ResearchAgent / StrategyResearchService 与 checkpoint 的集成。

运行方式：
  uv run pytest tests/integration/test_checkpoint_strategy_rd.py -v -s

前置条件：
  - Ollama 服务运行中（或 .env 配置 DashScope/OpenAI）
  - PostgreSQL 回测缓存有数据
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver

os.environ.setdefault("LONG_EARN_SKIP_CACHE_SYNC", "1")

from long_earn.strategy_rd.research_service import StrategyResearchService

load_dotenv()


@pytest.fixture(scope="module")
def context():
    """创建真实运行时上下文（训练集短区间）。"""
    from long_earn.config import AppConfig
    from long_earn.context_init import create_runtime_context

    config = AppConfig.from_env()
    config.max_iterations = 1
    config.max_workers = 1
    config.train_start_date = "2024-06-01"
    config.train_end_date = "2024-08-31"
    config.backtest_start_date = config.train_start_date
    config.backtest_end_date = config.train_end_date

    ctx = create_runtime_context(config)
    try:
        ctx.memory.initialize()
    except Exception as e:
        pytest.skip(f"知识库初始化失败: {e}")
    return ctx


class TestStrategyResearchServiceCheckpoint:
    """测试 StrategyResearchService.run_round 与 checkpoint 的集成"""

    def test_run_round_with_checkpoint_persists_state(self, context) -> None:
        checkpointer = MemorySaver()
        service = StrategyResearchService(context)

        thread_id = "test-svc-checkpoint"
        result = service.run_round(
            idea="研究一个基于ROE的选股策略",
            max_iterations=1,
            checkpointer=checkpointer,
            thread_id=thread_id,
        )

        assert result is not None
        assert result.backtest_result is not None
        assert isinstance(result.backtest_result, dict)

        from long_earn.strategy_rd.research_agent import ResearchAgent

        agent = ResearchAgent(context, checkpointer=checkpointer)
        thread_config = {"configurable": {"thread_id": thread_id}}
        is_completed = StrategyResearchService._thread_already_completed(
            agent._agent, thread_config
        )
        assert is_completed

    def test_run_round_reuses_completed_thread(self, context) -> None:
        checkpointer = MemorySaver()
        service = StrategyResearchService(context)
        thread_id = "test-svc-reuse"

        result1 = service.run_round(
            idea="研究一个基于ROE的选股策略",
            max_iterations=1,
            checkpointer=checkpointer,
            thread_id=thread_id,
        )
        assert result1 is not None

        result2 = service.run_round(
            idea="研究一个基于ROE的选股策略",
            max_iterations=1,
            checkpointer=checkpointer,
            thread_id=thread_id,
        )
        assert result2.backtest_result == result1.backtest_result

    def test_run_loop_with_checkpoint_multi_round(self, context) -> None:
        checkpointer = MemorySaver()
        service = StrategyResearchService(context)

        summary = service.run_loop(
            idea="研究一个基于ROE的选股策略",
            max_rounds=2,
            max_iterations=1,
            min_improvement=0.001,
            checkpointer=checkpointer,
            thread_id_prefix="test-loop",
        )

        assert summary is not None
        assert len(summary.rounds) > 0
        for rd in summary.rounds:
            assert rd.get("round") is not None
