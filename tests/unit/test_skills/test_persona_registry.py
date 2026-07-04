"""PersonaRegistry 与大师 Persona 单元测试 — ADR-012 Phase 1"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# 导入 skills.personas 包即触发 4 个内置大师注册
from long_earn.skills.personas import (
    BuffettPersona,
    CharlesMungerPersona,
    FiskePersona,
    PersonaContext,
    PersonaRegistry,
    PersonaResult,
    PetterPersona,
)
from long_earn.skills.personas.base import BasePersona


@pytest.fixture(autouse=True)
def restore_registry():
    """每个测试前后恢复注册表到初始状态，避免测试间互相污染。"""
    snapshot = dict(PersonaRegistry._personas)
    yield
    PersonaRegistry._personas = snapshot


class TestPersonaRegistry:
    """注册表基本行为。"""

    def test_register_and_get(self):
        """register 装饰器注册大师类，get 能取到。"""

        @PersonaRegistry.register
        class DummyPersona(BasePersona):
            name = "dummy"
            display_name = "测试大师"
            perspective = "测试"

        assert PersonaRegistry.get("dummy") is DummyPersona

    def test_create_all(self):
        """create_all 返回所有已注册大师的实例。"""
        mock_llm = MagicMock()
        instances = PersonaRegistry.create_all(llm=mock_llm)

        assert len(instances) >= 4
        for inst in instances.values():
            assert hasattr(inst, "analyze")
            assert hasattr(inst, "llm")
            assert inst.llm is mock_llm

    def test_4_built_in_personas_registered(self):
        """import skills.personas 后，注册表含 4 个内置大师。"""
        all_personas = PersonaRegistry.all()
        for name in ("buffett", "charles_munger", "fiske", "petter"):
            assert name in all_personas, f"缺少内置大师: {name}"

        assert PersonaRegistry.get("buffett") is BuffettPersona
        assert PersonaRegistry.get("charles_munger") is CharlesMungerPersona
        assert PersonaRegistry.get("fiske") is FiskePersona
        assert PersonaRegistry.get("petter") is PetterPersona


class TestBuffettPersonaAnalyze:
    """BuffettPersona stock_analysis 模式分析行为。"""

    def test_buffett_analyze_stock_analysis_mode(self):
        """mock LLM，调用 analyze 返回 PersonaResult。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = "建议买入，因为公司护城河坚固。"
        buffett = BuffettPersona(llm=mock_llm)

        ctx = PersonaContext(
            mode="stock_analysis",
            target={"symbol": "600519", "name": "贵州茅台"},
            event_context="央行降息",
        )
        result = buffett.analyze(ctx)

        assert isinstance(result, PersonaResult)
        # LLM 被调用一次
        mock_llm.invoke.assert_called_once()
        # raw_analysis 保留原始文本
        assert result.raw_analysis == "建议买入，因为公司护城河坚固。"
        # verdict 从文本中提取出"买入"
        assert result.verdict == "买入"
        # rationale 与原文一致
        assert result.rationale == mock_llm.invoke.return_value.content

    def test_buffett_analyze_invokes_llm_with_messages(self):
        """验证传给 LLM 的是消息列表（SystemMessage + few-shot + HumanMessage）。"""
        from langchain_core.messages import HumanMessage, SystemMessage

        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = "持有"
        buffett = BuffettPersona(llm=mock_llm)

        ctx = PersonaContext(
            mode="stock_analysis",
            target={"symbol": "AAPL"},
            event_context="",
        )
        buffett.analyze(ctx)

        msgs = mock_llm.invoke.call_args.args[0]
        assert isinstance(msgs, list)
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[-1], HumanMessage)
        # [System, ...examples, Human]
        assert len(msgs) == 1 + len(buffett.examples) + 1


class TestUnsupportedMode:
    """不支持的模式抛 NotImplementedError。"""

    def test_unsupported_mode_raises(self):
        """result_synthesis 模式尚未实现，应抛 NotImplementedError。"""
        mock_llm = MagicMock()
        buffett = BuffettPersona(llm=mock_llm)

        ctx = PersonaContext(
            mode="result_synthesis",
            target={},
        )
        with pytest.raises(NotImplementedError):
            buffett.analyze(ctx)

        # LLM 不应被调用
        mock_llm.invoke.assert_not_called()
