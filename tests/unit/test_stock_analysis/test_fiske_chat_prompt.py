"""FiskeAnalyst ChatPromptTemplate 迁移验证 — ADR-011 Phase 4"""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from long_earn.config import AppConfig, RuntimeContext
from long_earn.stock_analysis.agents.fiske_analyst import FiskeAnalyst


def _make_agent() -> FiskeAnalyst:
    """构造注入 Mock LLM 的 FiskeAnalyst（加载真实 fiske_prompt.md）。"""
    mock_llm = MagicMock()
    mock_llm.invoke.return_value.content = "费雪视角分析结果"
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
    return FiskeAnalyst(ctx)


class TestFiskeChatPrompt:
    """验证 format_messages 返回 [SystemMessage, ...examples, HumanMessage]。"""

    def test_format_messages_structure(self):
        agent = _make_agent()
        msgs = agent.prompt.format_messages(
            stock_data={"symbol": "688981"},
            event_context="研发投入超预期",
            examples=agent.examples,
        )
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[-1], HumanMessage)
        assert len(msgs) == 1 + len(agent.examples) + 1

    def test_system_message_contains_role(self):
        agent = _make_agent()
        msgs = agent.prompt.format_messages(
            stock_data={}, event_context="", examples=agent.examples
        )
        assert "费雪" in msgs[0].content
        assert "十五个要点" in msgs[0].content
        # few-shot 示例已从 system 移除
        assert "半导体" not in msgs[0].content

    def test_human_message_contains_stock_data(self):
        agent = _make_agent()
        msgs = agent.prompt.format_messages(
            stock_data={"symbol": "688981"},
            event_context="无相关事件",
            examples=agent.examples,
        )
        human = msgs[-1]
        assert "688981" in human.content
        assert "无相关事件" in human.content

    def test_examples_injected(self):
        agent = _make_agent()
        msgs = agent.prompt.format_messages(
            stock_data={}, event_context="", examples=agent.examples
        )
        assert isinstance(msgs[1], HumanMessage)
        assert msgs[1].content == agent.examples[0].content
        assert isinstance(msgs[2], AIMessage)
        assert msgs[2].content == agent.examples[1].content

    def test_analyze_invokes_llm_with_messages(self):
        agent = _make_agent()
        result = agent.analyze({"symbol": "688981"}, event_context="研发超预期")

        agent.llm.invoke.assert_called_once()
        msgs = agent.llm.invoke.call_args.args[0]
        assert isinstance(msgs, list)
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[-1], HumanMessage)
        assert result == "费雪视角分析结果"
