"""CharlesMungerAnalyst ChatPromptTemplate 迁移验证 — ADR-011 Phase 4"""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from long_earn.config import AppConfig, RuntimeContext
from long_earn.stock_analysis.agents.charles_munger_analyst import CharlesMungerAnalyst


def _make_agent() -> CharlesMungerAnalyst:
    """构造注入 Mock LLM 的 CharlesMungerAnalyst（加载真实 charles_munger_prompt.md）。"""
    mock_llm = MagicMock()
    mock_llm.invoke.return_value.content = "查理芒格视角分析结果"
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
    return CharlesMungerAnalyst(ctx)


class TestCharlesMungerChatPrompt:
    """验证 format_messages 返回 [SystemMessage, ...examples, HumanMessage]。"""

    def test_format_messages_structure(self):
        agent = _make_agent()
        msgs = agent.prompt.format_messages(
            stock_data={"symbol": "000333"},
            event_context="行业政策变化",
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
        assert "芒格" in msgs[0].content
        assert "多学科" in msgs[0].content
        # few-shot 示例已从 system 移除
        assert "沃尔玛" not in msgs[0].content

    def test_human_message_contains_stock_data(self):
        agent = _make_agent()
        msgs = agent.prompt.format_messages(
            stock_data={"symbol": "000333"},
            event_context="无相关事件",
            examples=agent.examples,
        )
        human = msgs[-1]
        assert "000333" in human.content
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
        result = agent.analyze({"symbol": "000333"}, event_context="政策变化")

        agent.llm.invoke.assert_called_once()
        msgs = agent.llm.invoke.call_args.args[0]
        assert isinstance(msgs, list)
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[-1], HumanMessage)
        assert result == "查理芒格视角分析结果"
