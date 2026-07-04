"""BuffettAnalyst ChatPromptTemplate 迁移验证 — ADR-011 Phase 4"""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from long_earn.config import AppConfig, RuntimeContext
from long_earn.stock_analysis.agents.buffett_analyst import BuffettAnalyst


def _make_agent() -> BuffettAnalyst:
    """构造注入 Mock LLM 的 BuffettAnalyst（加载真实 buffett_prompt.md）。"""
    mock_llm = MagicMock()
    mock_llm.invoke.return_value.content = "巴菲特视角分析结果"
    mock_llm_service = MagicMock()
    mock_llm_service.get_llm.return_value = mock_llm
    ctx = RuntimeContext(
        config=AppConfig(),
        logger=MagicMock(),
        monitoring=MagicMock(),
        llm_service=mock_llm_service,
        memory=MagicMock(),
        stock_service=MagicMock(),
        backtest_service=MagicMock(),
    )
    return BuffettAnalyst(ctx)


class TestBuffettChatPrompt:
    """验证 format_messages 返回 [SystemMessage, ...examples, HumanMessage]。"""

    def test_format_messages_structure(self):
        agent = _make_agent()
        msgs = agent.prompt.format_messages(
            stock_data={"symbol": "600519"},
            event_context="央行降息",
            examples=agent.examples,
        )
        # [SystemMessage, ...examples, HumanMessage]
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[-1], HumanMessage)
        assert len(msgs) == 1 + len(agent.examples) + 1

    def test_system_message_contains_role(self):
        agent = _make_agent()
        msgs = agent.prompt.format_messages(
            stock_data={}, event_context="", examples=agent.examples
        )
        assert "巴菲特" in msgs[0].content
        assert "分析框架" in msgs[0].content
        # few-shot 示例已从 system 移除，不应残留在 system 消息中
        assert "可口可乐" not in msgs[0].content

    def test_human_message_contains_stock_data(self):
        agent = _make_agent()
        msgs = agent.prompt.format_messages(
            stock_data={"symbol": "600519"},
            event_context="无相关事件",
            examples=agent.examples,
        )
        human = msgs[-1]
        assert "600519" in human.content
        assert "无相关事件" in human.content

    def test_examples_injected(self):
        agent = _make_agent()
        msgs = agent.prompt.format_messages(
            stock_data={}, event_context="", examples=agent.examples
        )
        # 第 2 条是第一个 example（HumanMessage），第 3 条是 AIMessage
        assert isinstance(msgs[1], HumanMessage)
        assert msgs[1].content == agent.examples[0].content
        assert isinstance(msgs[2], AIMessage)
        assert msgs[2].content == agent.examples[1].content

    def test_analyze_invokes_llm_with_messages(self):
        agent = _make_agent()
        result = agent.analyze({"symbol": "600519"}, event_context="降息")

        agent.llm.invoke.assert_called_once()
        msgs = agent.llm.invoke.call_args.args[0]
        assert isinstance(msgs, list)
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[-1], HumanMessage)
        assert result == "巴菲特视角分析结果"
