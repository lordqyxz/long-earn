"""主智能体单元测试（ADR-016 阶段 1）

验证工具集契约 + ReAct 编译 + 路由决策。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from long_earn.master_agent import (
    MasterAgent,
    _format_event_result,
    _format_stock_result,
    _format_strategy_result,
)

# ── 格式化辅助函数测试 ───────────────────────────────────────────


class TestFormatStrategyResult:
    """策略研发结果格式化"""

    def test_with_result_text(self) -> None:
        result = {"result": "策略研发完成", "strategy_name": "动量策略"}
        formatted = _format_strategy_result(result)
        assert "策略研发完成" in formatted
        assert "动量策略" in formatted

    def test_with_backtest_metrics(self) -> None:
        result = {
            "strategy_name": "双均线",
            "backtest_result": {
                "metrics": {
                    "total_return": 0.25,
                    "sharpe_ratio": 1.5,
                    "max_drawdown": -0.1,
                },
            },
        }
        formatted = _format_strategy_result(result)
        assert "双均线" in formatted
        assert "total_return: 0.25" in formatted
        assert "sharpe_ratio: 1.5" in formatted

    def test_empty_result(self) -> None:
        result: dict = {}
        formatted = _format_strategy_result(result)
        assert isinstance(formatted, str)
        assert len(formatted) > 0


class TestFormatStockResult:
    """股票分析结果格式化"""

    def test_with_summary(self) -> None:
        result = {"summary": "茅台基本面强劲"}
        formatted = _format_stock_result(result)
        assert formatted == "茅台基本面强劲"

    def test_with_error(self) -> None:
        result = {"error": "数据缺失"}
        formatted = _format_stock_result(result)
        assert "数据缺失" in formatted

    def test_empty_result(self) -> None:
        result: dict = {}
        formatted = _format_stock_result(result)
        assert isinstance(formatted, str)


class TestFormatEventResult:
    """事件推理结果格式化"""

    def test_with_summary(self) -> None:
        result = {"summary": "降息利好市场"}
        formatted = _format_event_result(result)
        assert formatted == "降息利好市场"

    def test_with_events_list(self) -> None:
        result = {
            "events": [
                {"title": "央行降息", "impact": "利好"},
                {"title": "贸易摩擦", "impact": "利空"},
            ],
        }
        formatted = _format_event_result(result)
        assert "央行降息" in formatted
        assert "利好" in formatted
        assert "贸易摩擦" in formatted

    def test_empty_result(self) -> None:
        result: dict = {}
        formatted = _format_event_result(result)
        assert isinstance(formatted, str)


# ── 工具集契约测试 ───────────────────────────────────────────────


class TestToolSetContract:
    """验证工具集契约：6 个工具、名称、描述"""

    @pytest.fixture
    def mock_master_agent(self) -> MasterAgent:
        """创建 MockMasterAgent（不实际创建子图 / ResearchAgent 图）"""
        with (
            patch(
                "long_earn.master_agent.ResearchAgent",
                return_value=MagicMock(),
            ),
            patch(
                "long_earn.master_agent.create_stock_analysis_subgraph",
                return_value=MagicMock(),
            ),
            patch(
                "long_earn.master_agent.create_event_inference_subgraph",
                return_value=MagicMock(),
            ),
            patch(
                "long_earn.master_agent.create_react_agent",
                return_value=MagicMock(),
            ),
            patch(
                "long_earn.master_agent.MarkdownPromptTemplate",
            ),
        ):
            ctx = MagicMock()
            ctx.logger = MagicMock()
            ctx.monitoring = MagicMock()
            ctx.memory = MagicMock()
            ctx.require_llm.return_value.get_llm.return_value = MagicMock()
            return MasterAgent(ctx)

    def test_six_tools_defined(self, mock_master_agent: MasterAgent) -> None:
        """验证 6 个任务工具全部定义"""
        tools = mock_master_agent._build_tools()
        assert len(tools) == 6

    def test_tool_names(self, mock_master_agent: MasterAgent) -> None:
        """验证工具名称"""
        tools = mock_master_agent._build_tools()
        names = [t.name for t in tools]
        assert "research_strategy" in names
        assert "analyze_stock" in names
        assert "infer_events" in names
        assert "retrieve_memory" in names
        assert "web_search" in names
        assert "summarize" in names

    def test_tool_descriptions_non_empty(self, mock_master_agent: MasterAgent) -> None:
        """验证每个工具都有非空描述"""
        tools = mock_master_agent._build_tools()
        for tool in tools:
            assert tool.description, f"工具 {tool.name} 描述为空"

    def test_research_strategy_params(self, mock_master_agent: MasterAgent) -> None:
        """验证 research_strategy 工具参数"""
        tools = mock_master_agent._build_tools()
        rs = next(t for t in tools if t.name == "research_strategy")
        schema = rs.args_schema.model_json_schema()
        properties = schema.get("properties", {})
        assert "idea" in properties
        assert "constraints" in properties
        # constraints 有默认值
        assert "default" in properties["constraints"]

    def test_analyze_stock_params(self, mock_master_agent: MasterAgent) -> None:
        """验证 analyze_stock 工具参数"""
        tools = mock_master_agent._build_tools()
        as_ = next(t for t in tools if t.name == "analyze_stock")
        schema = as_.args_schema.model_json_schema()
        properties = schema.get("properties", {})
        assert "query" in properties
        assert "symbols" in properties

    def test_react_agent_compiled(self, mock_master_agent: MasterAgent) -> None:
        """验证 ReAct agent 已编译"""
        assert mock_master_agent._agent is not None
