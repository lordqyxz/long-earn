"""LivermorePersona 单元测试 — ADR-012 Phase 4 扩展性验证

覆盖：
1. 自动注册到 PersonaRegistry
2. stock_analysis mode 调用与 verdict 提取
3. strategy_review mode 调用与 JSON 解析
4. strategy_generate mode 调用与 JSON 解析
5. 消息结构验证（SystemMessage + few-shot + HumanMessage）
6. 不支持的模式抛 NotImplementedError
7. 三个消费方均可通过 create_all 获取 livermore 实例
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from long_earn.skills.personas import (
    LivermorePersona,
    PersonaContext,
    PersonaRegistry,
    PersonaResult,
)

# ──────────────────────────────────────────────────────────────
# 1. 自动注册
# ──────────────────────────────────────────────────────────────


class TestLivermoreRegistration:
    """LivermorePersona 通过 @register 装饰器自动注册。"""

    def test_livermore_registered_in_registry(self):
        """import skills.personas 后，livermore 出现在注册表。"""
        assert "livermore" in PersonaRegistry.all()
        assert PersonaRegistry.get("livermore") is LivermorePersona

    def test_livermore_class_attributes(self):
        """类属性符合预期。"""
        assert LivermorePersona.name == "livermore"
        assert LivermorePersona.display_name == "杰西·利弗莫尔"
        assert LivermorePersona.perspective == "趋势交易"
        assert "stock_analysis" in LivermorePersona.supported_modes
        assert "strategy_review" in LivermorePersona.supported_modes
        assert "strategy_generate" in LivermorePersona.supported_modes

    def test_livermore_in_create_all(self):
        """create_all 返回的实例包含 livermore（三个消费方均通过此入口获取）。"""
        mock_llm = MagicMock()
        instances = PersonaRegistry.create_all(llm=mock_llm)
        assert "livermore" in instances
        assert isinstance(instances["livermore"], LivermorePersona)
        assert instances["livermore"].llm is mock_llm


# ──────────────────────────────────────────────────────────────
# 2. stock_analysis mode
# ──────────────────────────────────────────────────────────────


class TestLivermoreStockAnalysis:
    """LivermorePersona stock_analysis 模式行为。"""

    def test_stock_analysis_mode(self):
        """mock LLM 返回文本，调用 analyze 返回 PersonaResult。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = "建议买入，趋势确立且成交量配合。"
        livermore = LivermorePersona(llm=mock_llm)

        ctx = PersonaContext(
            mode="stock_analysis",
            target={"symbol": "600519", "name": "贵州茅台"},
            event_context="央行降息",
        )
        result = livermore.analyze(ctx)

        assert isinstance(result, PersonaResult)
        mock_llm.invoke.assert_called_once()
        assert result.verdict == "买入"
        assert "趋势" in result.rationale
        assert result.raw_analysis == "建议买入，趋势确立且成交量配合。"

    def test_stock_analysis_message_structure(self):
        """验证传给 LLM 的是消息列表（SystemMessage + few-shot + HumanMessage）。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = "持有，等待趋势确认。"
        livermore = LivermorePersona(llm=mock_llm)

        ctx = PersonaContext(
            mode="stock_analysis",
            target={"symbol": "000001"},
            event_context="",
        )
        livermore.analyze(ctx)

        messages = mock_llm.invoke.call_args[0][0]
        assert isinstance(messages, list)
        assert len(messages) >= 3
        assert isinstance(messages[0], SystemMessage)
        assert any(isinstance(m, HumanMessage) for m in messages)
        assert any(isinstance(m, AIMessage) for m in messages)


# ──────────────────────────────────────────────────────────────
# 3. strategy_review mode
# ──────────────────────────────────────────────────────────────


class TestLivermoreStrategyReview:
    """LivermorePersona strategy_review 模式行为。"""

    def test_strategy_review_mode(self):
        """mock LLM 返回 JSON，调用 analyze 返回 PersonaResult。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = json.dumps(
            {
                "verdict": "接受",
                "rationale": "策略基于趋势突破，符合顺势交易原则",
                "weaknesses": ["止损规则未明确"],
                "suggestions": ["加入 ATR 动态止损", "突破失败立即离场"],
                "confidence": 0.75,
            }
        )
        livermore = LivermorePersona(llm=mock_llm)

        ctx = PersonaContext(
            mode="strategy_review",
            target={"strategy_name": "趋势突破选股"},
            backtest_result={"total_return": 0.55, "max_drawdown": 0.22},
        )
        result = livermore.analyze(ctx)

        assert isinstance(result, PersonaResult)
        mock_llm.invoke.assert_called_once()
        assert result.verdict == "接受"
        assert "顺势交易" in result.rationale
        assert result.weaknesses == ["止损规则未明确"]
        assert result.suggestions == ["加入 ATR 动态止损", "突破失败立即离场"]
        assert result.confidence == pytest.approx(0.75)

    def test_strategy_review_invalid_json_degrades_gracefully(self):
        """LLM 返回非 JSON 时降级为 verdict=未知，不抛异常。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = "这不是 JSON，只是文本"
        livermore = LivermorePersona(llm=mock_llm)

        ctx = PersonaContext(
            mode="strategy_review",
            target={"strategy_name": "test"},
        )
        result = livermore.analyze(ctx)

        assert result.verdict == "未知"
        assert result.raw_analysis == "这不是 JSON，只是文本"


# ──────────────────────────────────────────────────────────────
# 4. strategy_generate mode
# ──────────────────────────────────────────────────────────────


class TestLivermoreStrategyGenerate:
    """LivermorePersona strategy_generate 模式行为。"""

    def test_strategy_generate_mode(self):
        """mock LLM 返回 JSON，调用 analyze 返回 PersonaResult。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = json.dumps(
            {
                "verdict": "推荐",
                "rationale": "趋势跟踪契合顺势交易原则，多头行情下胜率较高",
                "suggestions": ["20 日新高突破 + 成交量放大 1.5 倍", "跌破 10 日均线止损"],
                "confidence": 0.8,
            }
        )
        livermore = LivermorePersona(llm=mock_llm)

        ctx = PersonaContext(
            mode="strategy_generate",
            target={
                "query": "研究一个趋势跟踪选股策略",
                "knowledge_context": "市场处于多头行情",
            },
        )
        result = livermore.analyze(ctx)

        assert isinstance(result, PersonaResult)
        mock_llm.invoke.assert_called_once()
        assert result.verdict == "推荐"
        assert "顺势交易" in result.rationale
        assert result.suggestions == ["20 日新高突破 + 成交量放大 1.5 倍", "跌破 10 日均线止损"]
        assert result.confidence == pytest.approx(0.8)

    def test_strategy_generate_confidence_clamped(self):
        """confidence 超出 [0,1] 范围时被截断。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = json.dumps(
            {
                "verdict": "谨慎",
                "rationale": "ok",
                "suggestions": ["调仓"],
                "confidence": 1.5,
            }
        )
        livermore = LivermorePersona(llm=mock_llm)

        ctx = PersonaContext(
            mode="strategy_generate",
            target={"query": "test", "knowledge_context": ""},
        )
        result = livermore.analyze(ctx)

        assert result.confidence == 1.0


# ──────────────────────────────────────────────────────────────
# 5. 不支持的模式
# ──────────────────────────────────────────────────────────────


class TestLivermoreUnsupportedMode:
    """不支持的模式抛 NotImplementedError。"""

    def test_unsupported_mode_raises(self):
        """result_synthesis 模式尚未实现，应抛 NotImplementedError。"""
        mock_llm = MagicMock()
        livermore = LivermorePersona(llm=mock_llm)

        ctx = PersonaContext(
            mode="result_synthesis",
            target={},
        )
        with pytest.raises(NotImplementedError):
            livermore.analyze(ctx)
