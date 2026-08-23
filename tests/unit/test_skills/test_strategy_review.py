"""strategy_review mode 单元测试 — ADR-012 Phase 2

覆盖：
1. 单个大师 strategy_review mode 调用与 JSON 解析
2. 4 个大师均支持 strategy_review mode
3. StrategyResearchAgent.reflect 注入 master_perspectives 后行为
4. master_perspectives=None 时向后兼容
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


# ──────────────────────────────────────────────────────────────
# 3. StrategyResearchAgent.reflect 注入 master_perspectives
# ──────────────────────────────────────────────────────────────


def _make_mock_context() -> RuntimeContext:
    """构造注入 Mock LLM 的 RuntimeContext。"""
    mock_llm = MagicMock()
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

    mock_memory = MagicMock()
    # _get_knowledge_context 会 "\n".join(results)，必须返回 list 避免 TypeError
    mock_memory.search.return_value = []
    mock_memory.search_experience.return_value = []
    mock_memory.activate_events.return_value = []

    mock_config = MagicMock()
    mock_config.llm_type = "ollama"
    mock_config.llm_model = "test"
    mock_config.llm_base_url = "http://localhost"
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


class TestReflectWithMasterPerspectives:
    """reflect 接受 master_perspectives 后的行为。"""

    def test_reflect_with_master_perspectives(self):
        """注入 master_perspectives 后，反思 prompt 应包含大师视角文本。"""
        context = _make_mock_context()
        agent = StrategyResearchAgent(context=context)

        # 构造大师视角
        master_perspectives = {
            "buffett": PersonaResult(
                verdict="改进",
                rationale="缺乏护城河考量",
                weaknesses=["无基本面过滤"],
                suggestions=["加入 ROE 过滤"],
                confidence=0.8,
            ),
            "fiske": PersonaResult(
                verdict="拒绝",
                rationale="未捕捉成长性",
                weaknesses=["无研发投入因子"],
                suggestions=["纳入研发占比"],
                confidence=0.7,
            ),
        }

        result = agent.reflect(
            strategy={"strategy_name": "test"},
            backtest_result={"total_return": 0.1, "max_drawdown": 0.2},
            master_perspectives=master_perspectives,
        )

        # 返回结构不变
        assert "reflection" in result
        assert "improvement_suggestions" in result
        # LLM 被调用（每个 ToT 分支一次，共 3 个方向）
        assert context.llm_service.invoke.called
        # 检查至少一次调用的 prompt 含大师视角文本
        called_prompts = [
            call.args[0] if call.args else call.kwargs.get("prompt", "")
            for call in context.llm_service.invoke.call_args_list
        ]
        joined_prompts = "\n".join(str(p) for p in called_prompts)
        assert "master_perspectives" in joined_prompts
        assert "缺乏护城河考量" in joined_prompts
        assert "未捕捉成长性" in joined_prompts
        assert "buffett" in joined_prompts or "巴菲特" in joined_prompts

    def test_reflect_with_master_perspectives_dict_form(self):
        """master_perspectives 也接受 dict 值（兼容性）。"""
        context = _make_mock_context()
        agent = StrategyResearchAgent(context=context)

        master_perspectives = {
            "buffett": {
                "verdict": "改进",
                "rationale": "dict 形式视角",
                "weaknesses": ["w1"],
                "suggestions": ["s1"],
                "confidence": 0.5,
            }
        }

        result = agent.reflect(
            strategy={"strategy_name": "test"},
            backtest_result={"total_return": 0.1},
            master_perspectives=master_perspectives,  # type: ignore[arg-type]
        )

        assert "reflection" in result
        called_prompts = [
            call.args[0] if call.args else call.kwargs.get("prompt", "")
            for call in context.llm_service.invoke.call_args_list
        ]
        joined_prompts = "\n".join(str(p) for p in called_prompts)
        assert "dict 形式视角" in joined_prompts

    def test_reflect_without_master_perspectives_backwards_compatible(self):
        """master_perspectives=None 时，reflect 行为与原来完全一致。"""
        context = _make_mock_context()
        agent = StrategyResearchAgent(context=context)

        # 不传 master_perspectives
        result_no_master = agent.reflect(
            strategy={"strategy_name": "test"},
            backtest_result={"total_return": 0.1, "max_drawdown": 0.2},
        )

        # 重置 mock，再传入 None
        context.llm_service.reset_mock()
        result_none = agent.reflect(
            strategy={"strategy_name": "test"},
            backtest_result={"total_return": 0.1, "max_drawdown": 0.2},
            master_perspectives=None,
        )

        # 重置 mock，再传入空 dict
        context.llm_service.reset_mock()
        result_empty = agent.reflect(
            strategy={"strategy_name": "test"},
            backtest_result={"total_return": 0.1, "max_drawdown": 0.2},
            master_perspectives={},
        )

        # 三种调用结果应一致（reflection 文本与建议列表相同）
        assert result_no_master["reflection"] == result_none["reflection"]
        assert result_no_master["reflection"] == result_empty["reflection"]
        assert (
            result_no_master["improvement_suggestions"]
            == result_none["improvement_suggestions"]
            == result_empty["improvement_suggestions"]
        )

        # 不传 master_perspectives 时，prompt 不应包含 master_perspectives 段
        called_prompts = [
            call.args[0] if call.args else call.kwargs.get("prompt", "")
            for call in context.llm_service.invoke.call_args_list
        ]
        for p in called_prompts:
            assert "master_perspectives" not in str(p)

    def test_format_master_perspectives_empty(self):
        """空输入返回空串。"""
        context = _make_mock_context()
        agent = StrategyResearchAgent(context=context)

        assert agent._format_master_perspectives(None) == ""
        assert agent._format_master_perspectives({}) == ""

    def test_format_master_perspectives_with_persona_result(self):
        """PersonaResult 输入格式化为可读文本。"""
        context = _make_mock_context()
        agent = StrategyResearchAgent(context=context)

        view = PersonaResult(
            verdict="改进",
            rationale="策略需调整",
            weaknesses=["w1", "w2"],
            suggestions=["s1"],
            confidence=0.8,
        )
        text = agent._format_master_perspectives({"buffett": view})

        assert "buffett" in text
        assert "改进" in text
        assert "策略需调整" in text
        assert "w1" in text
        assert "w2" in text
        assert "s1" in text
        assert "0.8" in text
