"""strategy_generate mode 单元测试 — ADR-012 Phase 3

覆盖：
1. 单个大师 strategy_generate mode 调用与 JSON 解析
2. 4 个大师均支持 strategy_generate mode
3. StrategyResearchAgent.research_strategy_with_context 注入 master_hints 后行为
4. master_hints=None 时向后兼容
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from long_earn.config import RuntimeContext
from long_earn.skills.personas import (
    BuffettPersona,
    CharlesMungerPersona,
    FiskePersona,
    PersonaContext,
    PersonaResult,
    PetterPersona,
)
from long_earn.strategy_rd.agents.strategy_research_agent import StrategyResearchAgent

# ──────────────────────────────────────────────────────────────
# 1. 单个大师 strategy_generate mode
# ──────────────────────────────────────────────────────────────


class TestBuffettStrategyGenerate:
    """巴菲特 strategy_generate mode 行为。"""

    def test_buffett_strategy_generate_mode(self):
        """mock LLM 返回 JSON，调用 analyze 返回 PersonaResult。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = json.dumps(
            {
                "verdict": "推荐",
                "rationale": "查询方向契合价值投资原则，适合构建策略",
                "suggestions": ["以 ROE>15% 与 PE<10 双因子筛选", "月度调仓"],
                "confidence": 0.8,
            }
        )
        buffett = BuffettPersona(llm=mock_llm)

        ctx = PersonaContext(
            mode="strategy_generate",
            target={
                "query": "研究一个低估值蓝筹选股策略",
                "knowledge_context": "央行降息周期",
            },
        )
        result = buffett.analyze(ctx)

        assert isinstance(result, PersonaResult)
        # LLM 被调用一次
        mock_llm.invoke.assert_called_once()
        # verdict 解析为 "推荐"
        assert result.verdict == "推荐"
        assert "价值投资" in result.rationale
        assert result.suggestions == ["以 ROE>15% 与 PE<10 双因子筛选", "月度调仓"]
        assert result.confidence == pytest.approx(0.8)
        # raw_analysis 保留原始 JSON 文本
        assert "verdict" in result.raw_analysis

    def test_buffett_strategy_generate_invokes_llm_with_messages(self):
        """验证传给 LLM 的是消息列表（SystemMessage + few-shot + HumanMessage）。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = json.dumps(
            {"verdict": "谨慎", "rationale": "ok", "suggestions": ["调仓"]}
        )
        buffett = BuffettPersona(llm=mock_llm)

        ctx = PersonaContext(
            mode="strategy_generate",
            target={"query": "my_query_42", "knowledge_context": "ctx_99"},
        )
        buffett.analyze(ctx)

        msgs = mock_llm.invoke.call_args.args[0]
        assert isinstance(msgs, list)
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[-1], HumanMessage)
        # [System, ...strategy_generate_examples, Human]
        assert len(msgs) == 1 + len(buffett.strategy_generate_examples) + 1
        # human 消息应包含 query 与 knowledge_context
        human_content = msgs[-1].content
        assert "my_query_42" in human_content
        assert "ctx_99" in human_content

    def test_buffett_strategy_generate_invalid_json_fallback(self):
        """LLM 返回非 JSON 时，退化为 verdict="未知"，保留原文。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = "这不是一个有效的 JSON"
        buffett = BuffettPersona(llm=mock_llm)

        ctx = PersonaContext(
            mode="strategy_generate",
            target={"query": "test", "knowledge_context": ""},
        )
        result = buffett.analyze(ctx)

        assert isinstance(result, PersonaResult)
        assert result.verdict == "未知"
        assert result.rationale == "这不是一个有效的 JSON"
        assert result.raw_analysis == "这不是一个有效的 JSON"
        assert result.suggestions == []
        assert result.confidence == 0.0

    def test_buffett_strategy_generate_invalid_verdict_normalized(self):
        """verdict 不在合法取值内时归一化为 "未知"。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = json.dumps(
            {
                "verdict": "随便啦",
                "rationale": "x",
                "suggestions": [],
                "confidence": 2.0,  # 超出 1.0 应被截断
            }
        )
        buffett = BuffettPersona(llm=mock_llm)

        ctx = PersonaContext(
            mode="strategy_generate",
            target={"query": "test", "knowledge_context": ""},
        )
        result = buffett.analyze(ctx)

        assert result.verdict == "未知"
        # confidence 截断到 1.0
        assert result.confidence == 1.0

    def test_buffett_strategy_generate_verdict_values(self):
        """verdict 合法取值 推荐/谨慎/不推荐 均能正确解析。"""
        for verdict in ("推荐", "谨慎", "不推荐"):
            mock_llm = MagicMock()
            mock_llm.invoke.return_value.content = json.dumps(
                {"verdict": verdict, "rationale": "x", "suggestions": []}
            )
            buffett = BuffettPersona(llm=mock_llm)
            ctx = PersonaContext(
                mode="strategy_generate",
                target={"query": "q", "knowledge_context": ""},
            )
            result = buffett.analyze(ctx)
            assert result.verdict == verdict


# ──────────────────────────────────────────────────────────────
# 2. 4 个大师均支持 strategy_generate mode
# ──────────────────────────────────────────────────────────────


class TestAllPersonasSupportStrategyGenerate:
    """4 个大师都支持 strategy_generate mode。"""

    @pytest.mark.parametrize(
        "persona_cls,name,display_name",
        [
            (BuffettPersona, "buffett", "沃伦·巴菲特"),
            (CharlesMungerPersona, "charles_munger", "查理·芒格"),
            (FiskePersona, "fiske", "菲利普·费雪"),
            (PetterPersona, "petter", "彼得·林奇"),
        ],
    )
    def test_persona_supports_strategy_generate(
        self, persona_cls, name, display_name
    ):
        """大师 supported_modes 包含 strategy_generate。"""
        assert "strategy_generate" in persona_cls.supported_modes
        assert persona_cls.name == name
        assert persona_cls.display_name == display_name

    @pytest.mark.parametrize(
        "persona_cls",
        [BuffettPersona, CharlesMungerPersona, FiskePersona, PetterPersona],
    )
    def test_persona_strategy_generate_returns_persona_result(self, persona_cls):
        """每个大师 strategy_generate mode 调用后返回 PersonaResult。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = json.dumps(
            {
                "verdict": "推荐",
                "rationale": "策略符合该大师原则",
                "suggestions": ["tune param"],
                "confidence": 0.6,
            }
        )
        persona = persona_cls(llm=mock_llm)

        ctx = PersonaContext(
            mode="strategy_generate",
            target={"query": "test_query", "knowledge_context": "test_ctx"},
        )
        result = persona.analyze(ctx)

        assert isinstance(result, PersonaResult)
        assert result.verdict == "推荐"
        assert result.suggestions == ["tune param"]
        # LLM 被调用一次
        mock_llm.invoke.assert_called_once()
        # few-shot 示例非空（至少 2 个）
        assert len(persona.strategy_generate_examples) >= 2

    @pytest.mark.parametrize(
        "persona_cls",
        [BuffettPersona, CharlesMungerPersona, FiskePersona, PetterPersona],
    )
    def test_persona_strategy_generate_messages_contain_query(self, persona_cls):
        """传给 LLM 的 HumanMessage 含 query / knowledge_context。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = json.dumps(
            {"verdict": "推荐", "rationale": "ok", "suggestions": []}
        )
        persona = persona_cls(llm=mock_llm)

        ctx = PersonaContext(
            mode="strategy_generate",
            target={"query": "my_strategy_query_42", "knowledge_context": "kc_99"},
        )
        persona.analyze(ctx)

        msgs = mock_llm.invoke.call_args.args[0]
        human_content = msgs[-1].content
        assert "my_strategy_query_42" in human_content
        assert "kc_99" in human_content


# ──────────────────────────────────────────────────────────────
# 3. StrategyResearchAgent.research_strategy_with_context 注入 master_hints
# ──────────────────────────────────────────────────────────────


def _make_mock_context() -> RuntimeContext:
    """构造注入 Mock LLM 的 RuntimeContext。"""
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "生成的策略描述"
    mock_llm.invoke.return_value = mock_response

    mock_memory = MagicMock()
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
        llm_service=mock_llm,
        memory=mock_memory,
        stock_service=MagicMock(),
        backtest_service=MagicMock(),
        logger=MagicMock(),
        monitoring=MagicMock(),
        config=mock_config,
    )


class TestResearchWithMasterHints:
    """research_strategy_with_context 接受 master_hints 后的行为。"""

    def test_research_with_master_hints(self):
        """注入 master_hints 后，策略研究 prompt 应包含大师建议文本。"""
        context = _make_mock_context()
        agent = StrategyResearchAgent(context=context)

        master_hints = {
            "buffett": PersonaResult(
                verdict="推荐",
                rationale="契合价值投资原则",
                suggestions=["加入 ROE 过滤", "月度调仓"],
                confidence=0.8,
            ),
            "fiske": PersonaResult(
                verdict="谨慎",
                rationale="成长因子覆盖不足",
                suggestions=["纳入研发占比"],
                confidence=0.6,
            ),
        }

        result = agent.research_strategy_with_context(
            query="研究一个低估值策略",
            knowledge_context="市场处于降息周期",
            master_hints=master_hints,
        )

        # 返回结构不变
        assert result["strategy_name"] == "研究策略"
        assert result["description"] == "生成的策略描述"
        assert result["query"] == "研究一个低估值策略"
        # LLM 被调用一次
        context.llm_service.invoke.assert_called_once()
        # prompt 含大师建议文本
        called_prompt = context.llm_service.invoke.call_args.args[0]
        assert "buffett" in called_prompt or "巴菲特" in called_prompt
        assert "推荐" in called_prompt
        assert "契合价值投资原则" in called_prompt
        assert "加入 ROE 过滤" in called_prompt
        assert "fiske" in called_prompt or "费雪" in called_prompt
        assert "谨慎" in called_prompt

    def test_research_with_master_hints_dict_form(self):
        """master_hints 也接受 dict 值（兼容性）。"""
        context = _make_mock_context()
        agent = StrategyResearchAgent(context=context)

        master_hints = {
            "buffett": {
                "verdict": "推荐",
                "rationale": "dict 形式建议",
                "suggestions": ["s1"],
                "confidence": 0.5,
            }
        }

        result = agent.research_strategy_with_context(
            query="q", knowledge_context="k", master_hints=master_hints  # type: ignore[arg-type]
        )

        assert result["description"] == "生成的策略描述"
        called_prompt = context.llm_service.invoke.call_args.args[0]
        assert "dict 形式建议" in called_prompt

    def test_research_without_master_hints_backwards_compatible(self):
        """master_hints=None 时，research 行为与原来完全一致。"""
        context = _make_mock_context()
        agent = StrategyResearchAgent(context=context)

        # 不传 master_hints
        result_no_master = agent.research_strategy_with_context(
            query="q", knowledge_context="k"
        )
        prompt_no_master = context.llm_service.invoke.call_args.args[0]

        # 重置 mock，再传入 None
        context.llm_service.reset_mock()
        result_none = agent.research_strategy_with_context(
            query="q", knowledge_context="k", master_hints=None
        )
        prompt_none = context.llm_service.invoke.call_args.args[0]

        # 重置 mock，再传入空 dict
        context.llm_service.reset_mock()
        result_empty = agent.research_strategy_with_context(
            query="q", knowledge_context="k", master_hints={}
        )
        prompt_empty = context.llm_service.invoke.call_args.args[0]

        # 三种调用结果应一致
        assert result_no_master == result_none == result_empty
        # prompt 应完全一致（字节级），且不含 master_hints 字样
        assert prompt_no_master == prompt_none == prompt_empty
        assert "master_hints" not in prompt_no_master
        assert "投资大师建议" not in prompt_no_master

    def test_format_master_hints_empty(self):
        """空输入返回空串。"""
        context = _make_mock_context()
        agent = StrategyResearchAgent(context=context)

        assert agent._format_master_hints(None) == ""
        assert agent._format_master_hints({}) == ""

    def test_format_master_hints_with_persona_result(self):
        """PersonaResult 输入格式化为可读文本。"""
        context = _make_mock_context()
        agent = StrategyResearchAgent(context=context)

        view = PersonaResult(
            verdict="推荐",
            rationale="契合价值投资",
            suggestions=["s1", "s2"],
            confidence=0.8,
        )
        text = agent._format_master_hints({"buffett": view})

        assert "buffett" in text
        assert "推荐" in text
        assert "契合价值投资" in text
        assert "s1" in text
        assert "s2" in text
        assert "0.8" in text
        assert "建议" in text
