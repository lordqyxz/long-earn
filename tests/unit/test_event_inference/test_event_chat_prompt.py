"""event_inference agents ChatPromptTemplate 迁移验证 — ADR-011 Phase 4"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from langchain_core.messages import HumanMessage, SystemMessage

from long_earn.core.chat_prompt_loader import MarkdownChatPromptTemplate
from long_earn.event_inference.agents import LLMEventExtractor, LLMEventPropagator
from long_earn.event_inference.collectors.base import CollectedItem

# ── extract_prompt.md ────────────────────────────────────────────────


class TestExtractChatPrompt:
    """验证 extract_prompt.md 加载为 MarkdownChatPromptTemplate 的消息结构。"""

    def test_format_messages_returns_system_then_human(self):
        from long_earn.event_inference.agents import __file__ as agents_file

        prompt = MarkdownChatPromptTemplate(
            "extract_prompt.md", caller_file=agents_file
        )
        msgs = prompt.format_messages(items_json='[{"title": "茅台财报"}]')

        assert len(msgs) == 2
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[1], HumanMessage)

    def test_system_message_contains_role(self):
        from long_earn.event_inference.agents import __file__ as agents_file

        prompt = MarkdownChatPromptTemplate(
            "extract_prompt.md", caller_file=agents_file
        )
        msgs = prompt.format_messages(items_json="[]")

        assert "金融事件抽取器" in msgs[0].content
        # 抽取规则应位于 system 消息
        assert "抽取规则" in msgs[0].content
        # 输出格式与 JSON 示例应位于 system 消息
        assert "输出格式" in msgs[0].content
        assert "600519.SH" in msgs[0].content

    def test_human_message_contains_items_json(self):
        from long_earn.event_inference.agents import __file__ as agents_file

        prompt = MarkdownChatPromptTemplate(
            "extract_prompt.md", caller_file=agents_file
        )
        items_json = '[{"title": "宁德时代扩产", "content": "新增产能"}]'
        msgs = prompt.format_messages(items_json=items_json)

        human = msgs[1]
        assert "## 原始素材" in human.content
        assert "宁德时代扩产" in human.content
        # system 不应再含变量占位符渲染结果
        assert "{{ items_json }}" not in human.content

    def test_extractor_invokes_llm_with_messages(self):
        """LLMEventExtractor.extract 把消息列表传给 LLMService.invoke。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = "[]"
        extractor = LLMEventExtractor(mock_llm)

        items = [
            CollectedItem(
                title="茅台财报", content="净利润增长15%", source="fake"
            )
        ]
        extractor.extract(items)

        mock_llm.invoke.assert_called_once()
        call_args = mock_llm.invoke.call_args
        msgs = call_args.args[0]
        # format="json" 关键字参数保留
        assert call_args.kwargs.get("format") == "json"
        assert isinstance(msgs, list)
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[-1], HumanMessage)
        # human 消息含渲染后的 items_json
        assert "茅台财报" in msgs[-1].content


# ── propagate_prompt.md ──────────────────────────────────────────────


class TestPropagateChatPrompt:
    """验证 propagate_prompt.md 加载为 MarkdownChatPromptTemplate 的消息结构。"""

    def test_format_messages_returns_system_then_human(self):
        from long_earn.event_inference.agents import __file__ as agents_file

        prompt = MarkdownChatPromptTemplate(
            "propagate_prompt.md", caller_file=agents_file
        )
        msgs = prompt.format_messages(events_json='[{"content": "事件A"}]')

        assert len(msgs) == 2
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[1], HumanMessage)

    def test_system_message_contains_role(self):
        from long_earn.event_inference.agents import __file__ as agents_file

        prompt = MarkdownChatPromptTemplate(
            "propagate_prompt.md", caller_file=agents_file
        )
        msgs = prompt.format_messages(events_json="[]")

        assert "金融影响传播推理器" in msgs[0].content
        # 推理规则应位于 system 消息
        assert "推理规则" in msgs[0].content
        # 输出格式与 JSON 示例应位于 system 消息
        assert "输出格式" in msgs[0].content
        assert "propagates_to" in msgs[0].content

    def test_human_message_contains_events_json(self):
        from long_earn.event_inference.agents import __file__ as agents_file

        prompt = MarkdownChatPromptTemplate(
            "propagate_prompt.md", caller_file=agents_file
        )
        events_json = json.dumps(
            [{"content": "锂价上涨", "symbols": ["002594.SZ"]}],
            ensure_ascii=False,
        )
        msgs = prompt.format_messages(events_json=events_json)

        human = msgs[1]
        assert "## 事件列表" in human.content
        assert "锂价上涨" in human.content
        assert "{{ events_json }}" not in human.content

    def test_propagator_invokes_llm_with_messages(self):
        """LLMEventPropagator.propagate 把消息列表传给 LLMService.invoke。"""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value.content = "[]"
        propagator = LLMEventPropagator(mock_llm)

        events = [
            {
                "content": "茅台净利润增长15%",
                "keys": ["茅台"],
                "symbols": ["600519.SH"],
                "sentiment": "positive",
                "category": "财报",
                "confidence": 0.9,
            }
        ]
        propagator.propagate(events)

        mock_llm.invoke.assert_called_once()
        call_args = mock_llm.invoke.call_args
        msgs = call_args.args[0]
        assert call_args.kwargs.get("format") == "json"
        assert isinstance(msgs, list)
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[-1], HumanMessage)
        assert "茅台净利润增长15%" in msgs[-1].content


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v", "--no-cov"])
