"""strategy_review mode 单元测试 — ADR-012 Phase 2

覆盖：
1. 单个大师 strategy_review mode 调用与 JSON 解析
2. 4 个大师均支持 strategy_review mode
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
# 1. 单个大师 strategy_review mode
# ──────────────────────────────────────────────────────────────
class TestBuffettStrategyReview:
    """巴菲特 strategy_review mode 行为。"""

    def test_buffett_strategy_review_mode(self):
        """mock LLM 返回 JSON，调用 analyze 返回 PersonaResult。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = json.dumps(
            {
                "verdict": "改进",
                "rationale": "策略缺乏护城河考量，需调整",
                "weaknesses": ["无基本面过滤", "回撤失控"],
                "suggestions": ["加入 ROE 过滤", "设置止损"],
                "confidence": 0.75,
            }
        )
        buffett = BuffettPersona(llm=mock_llm)

        ctx = PersonaContext(
            mode="strategy_review",
            target={"strategy_name": "短线动量", "factors": ["mom_5d"]},
            backtest_result={"total_return": 0.4, "max_drawdown": 0.3},
            event_context="无",
        )
        result = buffett.analyze(ctx)

        assert isinstance(result, PersonaResult)
        # LLM 被调用一次
        mock_llm.invoke.assert_called_once()
        # verdict 解析为 "改进"
        assert result.verdict == "改进"
        assert "护城河" in result.rationale
        assert result.weaknesses == ["无基本面过滤", "回撤失控"]
        assert result.suggestions == ["加入 ROE 过滤", "设置止损"]
        assert result.confidence == pytest.approx(0.75)
        # raw_analysis 保留原始 JSON 文本
        assert "verdict" in result.raw_analysis

    def test_buffett_strategy_review_invokes_llm_with_messages(self):
        """验证传给 LLM 的是消息列表（SystemMessage + few-shot + HumanMessage）。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = json.dumps(
            {"verdict": "接受", "rationale": "ok", "weaknesses": [], "suggestions": []}
        )
        buffett = BuffettPersona(llm=mock_llm)

        ctx = PersonaContext(
            mode="strategy_review",
            target={"strategy_name": "test"},
            backtest_result={"total_return": 0.1},
        )
        buffett.analyze(ctx)

        msgs = mock_llm.invoke.call_args.args[0]
        assert isinstance(msgs, list)
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[-1], HumanMessage)
        # [System, ...strategy_review_examples, Human]
        assert len(msgs) == 1 + len(buffett.strategy_review_examples) + 1
        # human 消息应包含策略与回测结果
        human_content = msgs[-1].content
        assert "test" in human_content
        assert "total_return" in human_content

    def test_buffett_strategy_review_invalid_json_fallback(self):
        """LLM 返回非 JSON 时，退化为 verdict="未知"，保留原文。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = "这不是一个有效的 JSON"
        buffett = BuffettPersona(llm=mock_llm)

        ctx = PersonaContext(
            mode="strategy_review",
            target={"strategy_name": "test"},
        )
        result = buffett.analyze(ctx)

        assert isinstance(result, PersonaResult)
        assert result.verdict == "未知"
        assert result.rationale == "这不是一个有效的 JSON"
        assert result.raw_analysis == "这不是一个有效的 JSON"
        assert result.weaknesses == []
        assert result.suggestions == []
        assert result.confidence == 0.0

    def test_buffett_strategy_review_invalid_verdict_normalized(self):
        """verdict 不在合法取值内时归一化为 "未知"。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = json.dumps(
            {
                "verdict": "随便啦",
                "rationale": "x",
                "weaknesses": [],
                "suggestions": [],
                "confidence": 2.0,  # 超出 1.0 应被截断
            }
        )
        buffett = BuffettPersona(llm=mock_llm)

        ctx = PersonaContext(mode="strategy_review", target={})
        result = buffett.analyze(ctx)

        assert result.verdict == "未知"
        # confidence 截断到 1.0
        assert result.confidence == 1.0


# ──────────────────────────────────────────────────────────────
# 2. 4 个大师均支持 strategy_review mode
# ──────────────────────────────────────────────────────────────


class TestAllPersonasSupportStrategyReview:
    """4 个大师都支持 strategy_review mode。"""

    @pytest.mark.parametrize(
        "persona_cls,name,display_name",
        [
            (BuffettPersona, "buffett", "沃伦·巴菲特"),
            (CharlesMungerPersona, "charles_munger", "查理·芒格"),
            (FiskePersona, "fiske", "菲利普·费雪"),
            (PetterPersona, "petter", "彼得·林奇"),
        ],
    )
    def test_persona_supports_strategy_review(self, persona_cls, name, display_name):
        """大师 supported_modes 包含 strategy_review。"""
        assert "strategy_review" in persona_cls.supported_modes
        assert persona_cls.name == name
        assert persona_cls.display_name == display_name

    @pytest.mark.parametrize(
        "persona_cls",
        [BuffettPersona, CharlesMungerPersona, FiskePersona, PetterPersona],
    )
    def test_persona_strategy_review_returns_persona_result(self, persona_cls):
        """每个大师 strategy_review mode 调用后返回 PersonaResult。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = json.dumps(
            {
                "verdict": "接受",
                "rationale": "策略符合该大师原则",
                "weaknesses": ["minor"],
                "suggestions": ["tune param"],
                "confidence": 0.6,
            }
        )
        persona = persona_cls(llm=mock_llm)

        ctx = PersonaContext(
            mode="strategy_review",
            target={"strategy_name": "test"},
            backtest_result={"total_return": 0.2, "max_drawdown": 0.1},
        )
        result = persona.analyze(ctx)

        assert isinstance(result, PersonaResult)
        assert result.verdict == "接受"
        assert result.weaknesses == ["minor"]
        assert result.suggestions == ["tune param"]
        # LLM 被调用一次
        mock_llm.invoke.assert_called_once()
        # few-shot 示例非空
        assert len(persona.strategy_review_examples) >= 2

    @pytest.mark.parametrize(
        "persona_cls",
        [BuffettPersona, CharlesMungerPersona, FiskePersona, PetterPersona],
    )
    def test_persona_strategy_review_messages_contain_strategy(self, persona_cls):
        """传给 LLM 的 HumanMessage 含 strategy / backtest_result / event_context。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = json.dumps(
            {"verdict": "接受", "rationale": "ok", "weaknesses": [], "suggestions": []}
        )
        persona = persona_cls(llm=mock_llm)

        ctx = PersonaContext(
            mode="strategy_review",
            target={"strategy_name": "my_strategy_42"},
            backtest_result={"total_return": 0.555},
            event_context="央行降息_2024",
        )
        persona.analyze(ctx)

        msgs = mock_llm.invoke.call_args.args[0]
        human_content = msgs[-1].content
        assert "my_strategy_42" in human_content
        assert "0.555" in human_content
        assert "央行降息_2024" in human_content
