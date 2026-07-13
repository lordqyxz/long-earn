"""_reflection_node 调用大师的集成测试 — ADR-012 Phase 2

验证 _reflection_node：
1. 调用 4 个大师的 strategy_review mode
2. 把大师视角传给 research_agent.reflect 的 master_perspectives 参数
3. llm_service 为 None 时降级为原行为（不调用大师）
4. 单个大师调用失败时不阻塞反思流程
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from long_earn.config import RuntimeContext
from long_earn.services import (
    BacktestService,
    LLMService,
    LoggerService,
    MemoryService,
    MonitoringService,
    StockService,
)
from long_earn.skills.personas.protocol import PersonaContext, PersonaResult
from long_earn.strategy_rd.subgraph import _reflection_node


def _make_mock_llm_service() -> MagicMock:
    """构造 mock LLMService，invoke 返回合法 ToT 分支 JSON。"""
    mock_llm = MagicMock(spec=LLMService)
    mock_response = MagicMock()
    mock_response.content = json.dumps(
        {
            "direction": "收益增强",
            "reflection": "策略收益不足",
            "improvement_suggestions": [
                {"priority": "高", "issue": "因子单一", "suggestion": "增加因子"}
            ],
        }
    )
    mock_llm.invoke.return_value = mock_response
    # get_llm 返回底层 LLM mock（供 PersonaRegistry.create_all 使用）
    mock_llm.get_llm.return_value = MagicMock()
    return mock_llm


def _make_mock_context(llm_service: MagicMock | None = None) -> RuntimeContext:
    mock_memory = MagicMock(spec=MemoryService)
    mock_memory.search.return_value = []
    mock_memory.search_experience.return_value = []
    mock_memory.activate_events.return_value = []

    mock_config = MagicMock()
    mock_config.llm_type = "ollama"
    mock_config.llm_model = "test"
    mock_config.llm_base_url = "http://localhost"
    mock_config.memory_path = "~/.long_earn/substances.duckdb"
    mock_config.init_dir = "./init"

    return RuntimeContext(
        llm_service=llm_service or _make_mock_llm_service(),
        memory=mock_memory,
        stock_service=MagicMock(spec=StockService),
        backtest_service=MagicMock(spec=BacktestService),
        logger=MagicMock(spec=LoggerService),
        monitoring=MagicMock(spec=MonitoringService),
        config=mock_config,
    )


class _StubResearchAgent:
    """记录 reflect 调用参数的 stub。"""

    def __init__(self):
        self.reflect_calls: list[dict] = []

    def reflect(self, strategy, backtest_result, master_perspectives=None, history_return=0.0):
        self.reflect_calls.append(
            {
                "strategy": strategy,
                "backtest_result": backtest_result,
                "master_perspectives": master_perspectives,
                "history_return": history_return,
            }
        )
        return {
            "reflection": "test reflection",
            "improvement_suggestions": ["s1"],
            "explored_paths": [],
            "selected_direction": "收益增强",
            "tot_enabled": True,
        }


class TestReflectionNodeCallsPersonas:
    """_reflection_node 调用大师并传给 reflect。"""

    def test_reflection_node_calls_personas(self):
        """验证 _reflection_node 调用 4 个大师 strategy_review mode，
        并把结果传给 research_agent.reflect 的 master_perspectives。"""
        # 构造 4 个 mock persona
        mock_personas = {}
        for name in ("buffett", "charles_munger", "fiske", "petter"):
            persona = MagicMock()
            persona.analyze.return_value = PersonaResult(
                verdict="改进",
                rationale=f"{name} 视角",
                weaknesses=[f"{name}-w"],
                suggestions=[f"{name}-s"],
                confidence=0.7,
            )
            mock_personas[name] = persona

        llm_service = _make_mock_llm_service()
        stub_agent = _StubResearchAgent()
        logger = MagicMock(spec=LoggerService)

        state = {
            "strategy": {"strategy_name": "test"},
            "backtest_result": {"total_return": 0.1, "max_drawdown": 0.2},
        }

        with patch(
            "long_earn.skills.personas.PersonaRegistry.create_all",
            return_value=mock_personas,
        ):
            result = _reflection_node(
                state,
                research_agent=stub_agent,
                logger=logger,
                llm_service=llm_service,
            )

        # 4 个大师都被调用 strategy_review mode
        for name, persona in mock_personas.items():
            assert persona.analyze.called, f"大师 {name} 未被调用"
            call_kwargs = persona.analyze.call_args.args[0]
            assert isinstance(call_kwargs, PersonaContext)
            assert call_kwargs.mode == "strategy_review"
            assert call_kwargs.target == {"strategy_name": "test"}
            assert call_kwargs.backtest_result == {
                "total_return": 0.1,
                "max_drawdown": 0.2,
            }

        # reflect 被调用一次，且 master_perspectives 非空
        assert len(stub_agent.reflect_calls) == 1
        call = stub_agent.reflect_calls[0]
        assert call["master_perspectives"] is not None
        assert set(call["master_perspectives"].keys()) == {
            "buffett",
            "charles_munger",
            "fiske",
            "petter",
        }
        # 大师视角内容正确传入
        assert call["master_perspectives"]["buffett"].rationale == "buffett 视角"

        # 返回结构正确
        assert result["reflection"] == "test reflection"
        assert result["tot_enabled"] is True
        # logger 应记录大师审视完成
        assert logger.info.called

    def test_reflection_node_without_llm_service(self):
        """llm_service=None 时，不调用大师，master_perspectives 为 None。"""
        stub_agent = _StubResearchAgent()
        logger = MagicMock(spec=LoggerService)

        state = {
            "strategy": {"strategy_name": "test"},
            "backtest_result": {"total_return": 0.1},
        }

        result = _reflection_node(
            state,
            research_agent=stub_agent,
            logger=logger,
            llm_service=None,
        )

        # reflect 被调用，master_perspectives 为 None（降级）
        assert len(stub_agent.reflect_calls) == 1
        assert stub_agent.reflect_calls[0]["master_perspectives"] is None
        # 返回结构正确
        assert result["reflection"] == "test reflection"

    def test_reflection_node_persona_failure_does_not_block(self):
        """单个大师调用失败时不阻塞反思流程。"""
        # buffett 抛异常，其他正常
        mock_personas = {
            "buffett": MagicMock(**{"analyze.side_effect": RuntimeError("LLM 超时")}),
            "charles_munger": MagicMock(
                **{
                    "analyze.return_value": PersonaResult(
                        verdict="接受",
                        rationale="charles_munger ok",
                        weaknesses=[],
                        suggestions=[],
                        confidence=0.8,
                    )
                }
            ),
        }

        llm_service = _make_mock_llm_service()
        stub_agent = _StubResearchAgent()
        logger = MagicMock(spec=LoggerService)

        state = {
            "strategy": {"strategy_name": "test"},
            "backtest_result": {"total_return": 0.1},
        }

        with patch(
            "long_earn.skills.personas.PersonaRegistry.create_all",
            return_value=mock_personas,
        ):
            result = _reflection_node(
                state,
                research_agent=stub_agent,
                logger=logger,
                llm_service=llm_service,
            )

        # buffett 失败被跳过，charles_munger 成功
        assert mock_personas["buffett"].analyze.called
        assert mock_personas["charles_munger"].analyze.called
        # logger 记录 buffett 失败警告
        assert logger.warning.called

        # reflect 仍被调用，master_perspectives 含 charles_munger（非空）
        assert len(stub_agent.reflect_calls) == 1
        call = stub_agent.reflect_calls[0]
        assert call["master_perspectives"] is not None
        assert "charles_munger" in call["master_perspectives"]
        assert "buffett" not in call["master_perspectives"]

        # 反思仍正常返回
        assert result["reflection"] == "test reflection"

    def test_reflection_node_all_personas_fail_degrades(self):
        """所有大师都失败时，master_perspectives 为 None，反思降级为原行为。"""
        mock_personas = {
            "buffett": MagicMock(**{"analyze.side_effect": RuntimeError("err")}),
            "fiske": MagicMock(**{"analyze.side_effect": RuntimeError("err")}),
        }

        llm_service = _make_mock_llm_service()
        stub_agent = _StubResearchAgent()
        logger = MagicMock(spec=LoggerService)

        state = {
            "strategy": {"strategy_name": "test"},
            "backtest_result": {"total_return": 0.1},
        }

        with patch(
            "long_earn.skills.personas.PersonaRegistry.create_all",
            return_value=mock_personas,
        ):
            result = _reflection_node(
                state,
                research_agent=stub_agent,
                logger=logger,
                llm_service=llm_service,
            )

        # 所有大师都失败，master_perspectives 应为 None（空 dict 传 None）
        assert len(stub_agent.reflect_calls) == 1
        assert stub_agent.reflect_calls[0]["master_perspectives"] is None
        # 反思仍正常返回
        assert result["reflection"] == "test reflection"

    def test_reflection_node_registry_init_failure_degrades(self):
        """PersonaRegistry.create_all 抛异常时不阻塞反思流程。"""
        llm_service = _make_mock_llm_service()
        stub_agent = _StubResearchAgent()
        logger = MagicMock(spec=LoggerService)

        state = {
            "strategy": {"strategy_name": "test"},
            "backtest_result": {"total_return": 0.1},
        }

        with patch(
            "long_earn.skills.personas.PersonaRegistry.create_all",
            side_effect=RuntimeError("registry init failed"),
        ):
            result = _reflection_node(
                state,
                research_agent=stub_agent,
                logger=logger,
                llm_service=llm_service,
            )

        # 注册表初始化失败，master_perspectives 为 None
        assert len(stub_agent.reflect_calls) == 1
        assert stub_agent.reflect_calls[0]["master_perspectives"] is None
        # logger 记录警告
        assert logger.warning.called
        # 反思仍正常返回
        assert result["reflection"] == "test reflection"


class TestReflectionNodeEndToEndWithRealPersonas:
    """_reflection_node 与真实 PersonaRegistry 的端到端集成。

    验证：5 个真实大师类被注册并能被 _reflection_node 调用 strategy_review。
    LLM 仍 mock，重点验证注册表 → persona.analyze → reflect 链路畅通。
    """

    def test_reflection_node_with_real_persona_registry(self):
        """使用真实 PersonaRegistry，mock LLM 返回 JSON，验证 5 个大师被调用。"""
        # 导入 skills.personas 包即触发 5 个大师注册（4 内置 + livermore）

        # 构造 mock LLM，所有大师调用都返回合法 strategy_review JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = json.dumps(
            {
                "verdict": "改进",
                "rationale": "策略需调整",
                "weaknesses": ["w"],
                "suggestions": ["s"],
                "confidence": 0.6,
            }
        )

        llm_service = MagicMock(spec=LLMService)
        llm_service.get_llm.return_value = mock_llm

        stub_agent = _StubResearchAgent()
        logger = MagicMock(spec=LoggerService)

        state = {
            "strategy": {"strategy_name": "real_test"},
            "backtest_result": {"total_return": 0.05, "max_drawdown": 0.15},
        }

        result = _reflection_node(
            state,
            research_agent=stub_agent,
            logger=logger,
            llm_service=llm_service,
        )

        # 5 个真实大师都被调用（llm.invoke 被调用 5 次，每个大师一次）
        assert mock_llm.invoke.call_count == 5

        # reflect 被调用，master_perspectives 含 5 个大师
        assert len(stub_agent.reflect_calls) == 1
        call = stub_agent.reflect_calls[0]
        assert call["master_perspectives"] is not None
        assert set(call["master_perspectives"].keys()) == {
            "buffett",
            "charles_munger",
            "fiske",
            "livermore",
            "petter",
        }
        # 每个视角都是 PersonaResult
        for view in call["master_perspectives"].values():
            assert isinstance(view, PersonaResult)
            assert view.verdict == "改进"

        # 反思结果正常
        assert result["reflection"] == "test reflection"
