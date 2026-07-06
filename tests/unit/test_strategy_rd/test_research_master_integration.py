"""_research_node 调用大师的集成测试 — ADR-012 Phase 3

验证 _research_node：
1. 调用 4 个大师的 strategy_generate mode
2. 把大师视角传给 research_agent.research_strategy_with_context 的 master_hints 参数
3. llm_service 为 None 时降级为原行为（不调用大师）
4. 单个大师调用失败时不阻塞策略生成流程
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
from long_earn.strategy_rd.subgraph import _research_node


def _make_mock_llm_service() -> MagicMock:
    """构造 mock LLMService，invoke 返回策略描述文本。

    get_llm 返回底层 LLM mock（供 PersonaRegistry.create_all 使用）。
    """
    mock_llm = MagicMock(spec=LLMService)
    mock_response = MagicMock()
    mock_response.content = "生成的策略描述"
    mock_llm.invoke.return_value = mock_response
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
    mock_config.memory_path = "~/.long_earn/memory.npz"
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
    """记录 research_strategy_with_context 调用参数的 stub。"""

    def __init__(self):
        self.research_calls: list[dict] = []

    def research_strategy_with_context(
        self,
        query: str,
        knowledge_context: str = "",
        master_hints=None,
    ):
        self.research_calls.append(
            {
                "query": query,
                "knowledge_context": knowledge_context,
                "master_hints": master_hints,
            }
        )
        return {
            "strategy_name": "研究策略",
            "description": "生成的策略描述",
            "query": query,
        }


class TestResearchNodeCallsPersonas:
    """_research_node 调用大师并传给 research_strategy_with_context。"""

    def test_research_node_calls_personas(self):
        """验证 _research_node 调用 4 个大师 strategy_generate mode，
        并把结果传给 research_agent.research_strategy_with_context 的 master_hints。"""
        # 构造 4 个 mock persona
        mock_personas = {}
        for name in ("buffett", "charles_munger", "fiske", "petter"):
            persona = MagicMock()
            persona.analyze.return_value = PersonaResult(
                verdict="推荐",
                rationale=f"{name} 建议视角",
                suggestions=[f"{name}-suggestion"],
                confidence=0.7,
            )
            mock_personas[name] = persona

        llm_service = _make_mock_llm_service()
        stub_agent = _StubResearchAgent()
        logger = MagicMock(spec=LoggerService)

        state = {
            "query": "研究一个低估值蓝筹策略",
            "knowledge_context": "央行降息周期",
        }

        with patch(
            "long_earn.skills.personas.PersonaRegistry.create_all",
            return_value=mock_personas,
        ):
            result = _research_node(
                state,
                research_agent=stub_agent,
                logger=logger,
                llm_service=llm_service,
            )

        # 4 个大师都被调用 strategy_generate mode
        for name, persona in mock_personas.items():
            assert persona.analyze.called, f"大师 {name} 未被调用"
            call_kwargs = persona.analyze.call_args.args[0]
            assert isinstance(call_kwargs, PersonaContext)
            assert call_kwargs.mode == "strategy_generate"
            assert call_kwargs.target == {
                "query": "研究一个低估值蓝筹策略",
                "knowledge_context": "央行降息周期",
            }

        # research_strategy_with_context 被调用一次，且 master_hints 非空
        assert len(stub_agent.research_calls) == 1
        call = stub_agent.research_calls[0]
        assert call["query"] == "研究一个低估值蓝筹策略"
        assert call["knowledge_context"] == "央行降息周期"
        assert call["master_hints"] is not None
        assert set(call["master_hints"].keys()) == {
            "buffett",
            "charles_munger",
            "fiske",
            "petter",
        }
        # 大师视角内容正确传入
        assert call["master_hints"]["buffett"].rationale == "buffett 建议视角"

        # 返回结构正确
        assert result["strategy_name"] == "研究策略"
        assert result["design_rationale"] == "生成的策略描述"
        # logger 应记录大师建议完成
        assert logger.info.called

    def test_research_node_llm_service_none_degrades(self):
        """llm_service=None 时，不调用大师，master_hints 为 None（降级为原行为）。"""
        stub_agent = _StubResearchAgent()
        logger = MagicMock(spec=LoggerService)

        state = {
            "query": "研究一个测试策略",
            "knowledge_context": "无",
        }

        result = _research_node(
            state,
            research_agent=stub_agent,
            logger=logger,
            llm_service=None,
        )

        # research_strategy_with_context 被调用，master_hints 为 None（降级）
        assert len(stub_agent.research_calls) == 1
        assert stub_agent.research_calls[0]["master_hints"] is None
        assert stub_agent.research_calls[0]["query"] == "研究一个测试策略"
        # 返回结构正确
        assert result["strategy_name"] == "研究策略"
        assert result["design_rationale"] == "生成的策略描述"

    def test_research_node_persona_failure_does_not_block(self):
        """单个大师调用失败时不阻塞策略生成流程。"""
        # buffett 抛异常，其他正常
        mock_personas = {
            "buffett": MagicMock(**{"analyze.side_effect": RuntimeError("LLM 超时")}),
            "fiske": MagicMock(
                **{
                    "analyze.return_value": PersonaResult(
                        verdict="推荐",
                        rationale="fiske ok",
                        suggestions=["纳入研发占比"],
                        confidence=0.8,
                    )
                }
            ),
        }

        llm_service = _make_mock_llm_service()
        stub_agent = _StubResearchAgent()
        logger = MagicMock(spec=LoggerService)

        state = {
            "query": "研究一个成长股策略",
            "knowledge_context": "产业政策利好",
        }

        with patch(
            "long_earn.skills.personas.PersonaRegistry.create_all",
            return_value=mock_personas,
        ):
            result = _research_node(
                state,
                research_agent=stub_agent,
                logger=logger,
                llm_service=llm_service,
            )

        # buffett 失败被跳过，fiske 成功
        assert mock_personas["buffett"].analyze.called
        assert mock_personas["fiske"].analyze.called
        # logger 记录 buffett 失败警告
        assert logger.warning.called

        # research_strategy_with_context 仍被调用，master_hints 含 fiske（非空）
        assert len(stub_agent.research_calls) == 1
        call = stub_agent.research_calls[0]
        assert call["master_hints"] is not None
        assert "fiske" in call["master_hints"]
        assert "buffett" not in call["master_hints"]

        # 策略仍正常返回
        assert result["strategy_name"] == "研究策略"

    def test_research_node_all_personas_fail_degrades(self):
        """所有大师都失败时，master_hints 为 None，策略生成降级为原行为。"""
        mock_personas = {
            "buffett": MagicMock(**{"analyze.side_effect": RuntimeError("err")}),
            "fiske": MagicMock(**{"analyze.side_effect": RuntimeError("err")}),
        }

        llm_service = _make_mock_llm_service()
        stub_agent = _StubResearchAgent()
        logger = MagicMock(spec=LoggerService)

        state = {
            "query": "研究一个测试策略",
            "knowledge_context": "",
        }

        with patch(
            "long_earn.skills.personas.PersonaRegistry.create_all",
            return_value=mock_personas,
        ):
            result = _research_node(
                state,
                research_agent=stub_agent,
                logger=logger,
                llm_service=llm_service,
            )

        # 所有大师都失败，master_hints 应为 None（空 dict 传 None）
        assert len(stub_agent.research_calls) == 1
        assert stub_agent.research_calls[0]["master_hints"] is None
        # 策略仍正常返回
        assert result["strategy_name"] == "研究策略"

    def test_research_node_registry_init_failure_degrades(self):
        """PersonaRegistry.create_all 抛异常时不阻塞策略生成流程。"""
        llm_service = _make_mock_llm_service()
        stub_agent = _StubResearchAgent()
        logger = MagicMock(spec=LoggerService)

        state = {
            "query": "研究一个测试策略",
            "knowledge_context": "",
        }

        with patch(
            "long_earn.skills.personas.PersonaRegistry.create_all",
            side_effect=RuntimeError("registry init failed"),
        ):
            result = _research_node(
                state,
                research_agent=stub_agent,
                logger=logger,
                llm_service=llm_service,
            )

        # 注册表初始化失败，master_hints 为 None
        assert len(stub_agent.research_calls) == 1
        assert stub_agent.research_calls[0]["master_hints"] is None
        # logger 记录警告
        assert logger.warning.called
        # 策略仍正常返回
        assert result["strategy_name"] == "研究策略"


class TestResearchNodeEndToEndWithRealPersonas:
    """_research_node 与真实 PersonaRegistry 的端到端集成。

    验证：5 个真实大师类被注册并能被 _research_node 调用 strategy_generate。
    LLM 仍 mock，重点验证注册表 → persona.analyze → research 链路畅通。
    """

    def test_research_node_with_real_persona_registry(self):
        """使用真实 PersonaRegistry，mock LLM 返回 JSON，验证 5 个大师被调用。"""
        # 导入 skills.personas 包即触发 5 个大师注册（4 内置 + livermore）

        # 构造 mock LLM，所有大师调用都返回合法 strategy_generate JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = json.dumps(
            {
                "verdict": "推荐",
                "rationale": "策略方向契合大师原则",
                "suggestions": ["加入因子"],
                "confidence": 0.6,
            }
        )

        llm_service = MagicMock(spec=LLMService)
        llm_service.get_llm.return_value = mock_llm
        # research_strategy_with_context 内部 llm_service.invoke 也要返回内容
        research_response = MagicMock()
        research_response.content = "生成的策略描述"
        llm_service.invoke.return_value = research_response

        stub_agent = _StubResearchAgent()
        logger = MagicMock(spec=LoggerService)

        state = {
            "query": "研究一个低估值策略",
            "knowledge_context": "降息周期",
        }

        result = _research_node(
            state,
            research_agent=stub_agent,
            logger=logger,
            llm_service=llm_service,
        )

        # 5 个真实大师都被调用（底层 mock_llm.invoke 被调用 5 次，每个大师一次）
        assert mock_llm.invoke.call_count == 5

        # research_strategy_with_context 被调用，master_hints 含 5 个大师
        assert len(stub_agent.research_calls) == 1
        call = stub_agent.research_calls[0]
        assert call["master_hints"] is not None
        assert set(call["master_hints"].keys()) == {
            "buffett",
            "charles_munger",
            "fiske",
            "livermore",
            "petter",
        }
        # 每个视角都是 PersonaResult
        for view in call["master_hints"].values():
            assert isinstance(view, PersonaResult)
            assert view.verdict == "推荐"

        # 策略结果正常
        assert result["strategy_name"] == "研究策略"
        assert result["design_rationale"] == "生成的策略描述"
