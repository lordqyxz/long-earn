"""strategy_generate mode 单元测试 — ADR-012 Phase 3

覆盖：
1. 单个大师 strategy_generate mode 调用与 JSON 解析
2. 4 个大师均支持 strategy_generate mode
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from long_earn.skills.personas import (
    BuffettPersona,
    CharlesMungerPersona,
    FiskePersona,
    PersonaContext,
    PersonaResult,
    PetterPersona,
)


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
    """4 个大师均声明 strategy_generate mode。"""

    @pytest.mark.parametrize(
        "persona_cls,name,display_name",
        [
            (BuffettPersona, "buffett", "沃伦·巴菲特"),
            (CharlesMungerPersona, "charles_munger", "查理·芒格"),
            (FiskePersona, "fiske", "菲利普·费雪"),
            (PetterPersona, "petter", "彼得·林奇"),
        ],
    )
    def test_persona_supports_strategy_generate(self, persona_cls, name, display_name):
        """supported_modes 包含 strategy_generate。"""
        assert "strategy_generate" in persona_cls.supported_modes
        assert persona_cls.name == name
        assert persona_cls.display_name == display_name

    @pytest.mark.parametrize(
        "persona_cls",
        [BuffettPersona, CharlesMungerPersona, FiskePersona, PetterPersona],
    )
    def test_persona_analyze_strategy_generate(self, persona_cls):
        """各大师 strategy_generate analyze 可解析 mock JSON。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = json.dumps(
            {
                "verdict": "推荐",
                "rationale": "ok",
                "suggestions": ["a"],
                "confidence": 0.8,
            }
        )
        persona = persona_cls(llm=mock_llm)
        ctx = PersonaContext(
            mode="strategy_generate",
            target={"query": "q", "knowledge_context": ""},
        )
        result = persona.analyze(ctx)
        assert isinstance(result, PersonaResult)
        assert result.verdict == "推荐"
