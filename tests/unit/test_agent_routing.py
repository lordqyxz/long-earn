"""agent.py 主图路由/汇总节点 ChatPromptTemplate 迁移验证 — ADR-011 Phase 4"""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from long_earn.agent import (
    ROUTING_CHAT_PROMPT,
    SUMMARIZE_CHAT_PROMPT,
    _intent_analyze_node,
    _summarize_node,
)


# ── 夹具 ──────────────────────────────────────────────────────────────


def _mock_monitoring():
    """构造返回 context manager 的 MonitoringService mock。"""
    m = MagicMock()

    @contextmanager
    def _ctx(_name: str):
        yield

    m.track.side_effect = _ctx
    return m


def _make_llm_response(content: str, usage: dict | None = None) -> MagicMock:
    """构造 LLM 响应 mock。"""
    resp = MagicMock()
    resp.content = content
    resp.usage_metadata = usage
    return resp


# ── ROUTING_CHAT_PROMPT 消息结构 ──────────────────────────────────────


class TestRoutingChatPrompt:
    """验证 ROUTING_CHAT_PROMPT 的消息结构。"""

    def test_format_messages_returns_system_then_human(self):
        msgs = ROUTING_CHAT_PROMPT.format_messages(user_query="茅台分析")
        assert len(msgs) == 2
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[1], HumanMessage)

    def test_system_message_contains_routing_instruction(self):
        msgs = ROUTING_CHAT_PROMPT.format_messages(user_query="x")
        sys_content = msgs[0].content
        # system 应含路由指令与可用子图说明
        assert "strategy_rd" in sys_content
        assert "stock_analysis" in sys_content
        assert "event_inference" in sys_content
        assert "JSON" in sys_content
        # system 不应含用户查询变量（变量在 human 消息中）
        assert "{{ user_query }}" not in sys_content

    def test_human_message_contains_user_query(self):
        msgs = ROUTING_CHAT_PROMPT.format_messages(user_query="分析贵州茅台")
        human = msgs[1]
        assert "分析贵州茅台" in human.content
        assert "{{ user_query }}" not in human.content

    def test_no_html_escape_in_user_query(self):
        """jinja2 默认不 HTML 转义用户查询内容。"""
        msgs = ROUTING_CHAT_PROMPT.format_messages(user_query="<茅台>&\"")
        assert "<茅台>" in msgs[1].content
        assert "&lt;" not in msgs[1].content
        assert "&gt;" not in msgs[1].content


# ── SUMMARIZE_CHAT_PROMPT 消息结构 ────────────────────────────────────


class TestSummarizeChatPrompt:
    """验证 SUMMARIZE_CHAT_PROMPT 的消息结构。"""

    def test_format_messages_returns_system_then_human(self):
        msgs = SUMMARIZE_CHAT_PROMPT.format_messages(
            user_query="查询",
            routing_reason="股票分析",
            strategy_result="无",
            stock_analysis_result="无",
            event_inference_result="无",
        )
        assert len(msgs) == 2
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[1], HumanMessage)

    def test_system_message_contains_summary_instruction(self):
        msgs = SUMMARIZE_CHAT_PROMPT.format_messages(
            user_query="x",
            routing_reason="",
            strategy_result="",
            stock_analysis_result="",
            event_inference_result="",
        )
        sys_content = msgs[0].content
        assert "证据详实" in sys_content
        assert "表格" in sys_content
        # system 不应含任何变量占位符
        for var in (
            "{{ user_query }}",
            "{{ routing_reason }}",
            "{{ strategy_result }}",
            "{{ stock_analysis_result }}",
            "{{ event_inference_result }}",
        ):
            assert var not in sys_content

    def test_human_message_contains_all_fields(self):
        msgs = SUMMARIZE_CHAT_PROMPT.format_messages(
            user_query="茅台买入区间",
            routing_reason="股票分析",
            strategy_result="策略A",
            stock_analysis_result="巴菲特视角结果",
            event_inference_result="事件X",
        )
        human = msgs[1].content
        assert "茅台买入区间" in human
        assert "股票分析" in human
        assert "策略A" in human
        assert "巴菲特视角结果" in human
        assert "事件X" in human


# ── _intent_analyze_node 集成（mock LLM）──────────────────────────────


class TestIntentAnalyzeNode:
    """验证 _intent_analyze_node 用消息列表调用 LLM 并解析路由。"""

    def test_invokes_llm_with_message_list(self):
        """LLM 被调用时收到的应是 list[BaseMessage]，而非字符串。"""
        llm_service = MagicMock()
        llm_service.invoke.return_value = _make_llm_response(
            json.dumps({"route": "stock_analysis", "reason": "股票查询"})
        )
        monitoring = _mock_monitoring()
        logger = MagicMock()
        context = MagicMock()
        context.config = MagicMock()
        context.config.strategy_keywords = ()
        context.config.stock_analysis_keywords = ()
        context.config.event_inference_keywords = ()

        state = {"user_query": "分析贵州茅台"}
        result = _intent_analyze_node(state, llm_service, logger, monitoring, context)

        llm_service.invoke.assert_called_once()
        call_args = llm_service.invoke.call_args
        msgs = call_args.args[0]
        assert isinstance(msgs, list)
        assert all(not isinstance(m, str) for m in msgs)
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[-1], HumanMessage)
        # format="json" 参数保留
        assert call_args.kwargs.get("format") == "json"
        # human 消息含用户查询
        assert "分析贵州茅台" in msgs[-1].content
        # 路由决策被正确解析
        assert result["route"] == "stock_analysis"
        assert result["routing_reason"] == "股票查询"

    def test_empty_query_returns_unknown(self):
        """空查询直接返回 unknown，不调用 LLM。"""
        llm_service = MagicMock()
        monitoring = _mock_monitoring()
        logger = MagicMock()
        context = MagicMock()

        state = {"user_query": ""}
        result = _intent_analyze_node(state, llm_service, logger, monitoring, context)

        llm_service.invoke.assert_not_called()
        assert result["route"] == "unknown"
        assert "error" in result

    def test_invalid_route_falls_back_to_keyword_match(self):
        """LLM 返回无效 route 时降级到关键词匹配。"""
        llm_service = MagicMock()
        llm_service.invoke.return_value = _make_llm_response(
            json.dumps({"route": "invalid_route", "reason": "x"})
        )
        monitoring = _mock_monitoring()
        logger = MagicMock()
        context = MagicMock()
        context.config = MagicMock()
        context.config.strategy_keywords = ("策略",)
        context.config.stock_analysis_keywords = ("股票",)
        context.config.event_inference_keywords = ("新闻",)

        state = {"user_query": "请分析这只股票"}
        result = _intent_analyze_node(state, llm_service, logger, monitoring, context)

        # 降级到关键词匹配
        assert result["route"] == "stock_analysis"
        assert "关键词匹配" in result["routing_reason"]


# ── _summarize_node 集成（mock LLM）───────────────────────────────────


class TestSummarizeNode:
    """验证 _summarize_node 用消息列表调用 LLM。"""

    def test_invokes_llm_with_message_list(self):
        llm_service = MagicMock()
        llm_service.invoke.return_value = _make_llm_response("汇总结果文本")
        monitoring = _mock_monitoring()
        logger = MagicMock()

        state = {
            "user_query": "茅台买入区间",
            "routing_reason": "股票分析",
            "strategy_result": None,
            "stock_analysis_result": {"summary": "巴菲特视角"},
            "event_inference_result": None,
        }
        result = _summarize_node(state, llm_service, logger, monitoring)

        llm_service.invoke.assert_called_once()
        call_args = llm_service.invoke.call_args
        msgs = call_args.args[0]
        assert isinstance(msgs, list)
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[-1], HumanMessage)
        # summarize 调用不传 format="json"
        assert "format" not in call_args.kwargs or call_args.kwargs.get("format") == ""
        # human 消息含原始问题与股票分析结果
        assert "茅台买入区间" in msgs[-1].content
        assert "巴菲特视角" in msgs[-1].content
        assert result["summary"] == "汇总结果文本"

    def test_no_results_returns_apology(self):
        """三路结果全空时直接返回致歉文案，不调用 LLM。"""
        llm_service = MagicMock()
        monitoring = _mock_monitoring()
        logger = MagicMock()

        state = {
            "user_query": "x",
            "routing_reason": "",
            "strategy_result": None,
            "stock_analysis_result": None,
            "event_inference_result": None,
        }
        result = _summarize_node(state, llm_service, logger, monitoring)

        llm_service.invoke.assert_not_called()
        assert "无法处理" in result["summary"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
