"""主智能体单元测试（ADR-024 §C 工具分层）

验证结构化输出契约 + 工具集契约 + 各工具执行路径 + ReAct 编译。
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from long_earn.master_agent import MasterAgent
from long_earn.master_agent_tools import (
    QueryEventsOutput,
    QueryMarketOutput,
    RunEventCollectionOutput,
    ToolOutput,
    build_master_tools,
)
from long_earn.ontology import ConceptResolution, ConceptResult, OntologyNode


def _payload(text: str) -> dict[str, Any]:
    """解析工具输出中的 <details> JSON 段。"""
    body = text.split("<details>\n", 1)[1].rsplit("\n</details>", 1)[0]
    parsed: dict[str, Any] = json.loads(body)
    return parsed


def _make_context() -> MagicMock:
    """构造工具执行测试用的 mock 运行时上下文。"""
    ctx = MagicMock()
    ctx.logger = MagicMock()
    ctx.monitoring = MagicMock()
    ctx.memory = MagicMock()
    ctx.connector = None
    ctx.realtime_provider = None
    return ctx


def _build_tools(ctx: MagicMock) -> list[Any]:
    return build_master_tools(
        ctx,
        research_agent=MagicMock(),
        stock_analysis_subgraph=MagicMock(),
        event_inference_subgraph=MagicMock(),
    )


def _tool_by_name(tools: list[Any], name: str) -> Any:
    return next(t for t in tools if t.name == name)


# ── 结构化输出契约测试 ───────────────────────────────────────────


class TestToolOutputContract:
    """ToolOutput 渲染契约：摘要 + <details> 结构化详情两段"""

    def test_render_two_sections(self) -> None:
        out = QueryEventsOutput(
            summary="激活 2 条相关事件物质",
            query="茅台",
            items=["e1", "e2"],
        )
        text = out.render()
        assert text.startswith("激活 2 条相关事件物质")
        assert "<details>" in text and "</details>" in text
        payload = _payload(text)
        assert payload["tool"] == "query_events"
        assert payload["query"] == "茅台"
        assert payload["items"] == ["e1", "e2"]
        assert "summary" not in payload

    def test_render_filters_empty_fields(self) -> None:
        out = QueryMarketOutput(summary="实时行情源不可用", quotes=[])
        payload = _payload(out.render())
        assert "quotes" not in payload
        assert payload["tool"] == "query_market"

    def test_tool_key_naming(self) -> None:
        assert (
            _payload(
                RunEventCollectionOutput(
                    summary="s",
                    collected_count=1,
                    event_count=1,
                    relation_count=0,
                    saved_count=1,
                    events=[],
                ).render()
            )["tool"]
            == "run_event_collection"
        )

    def test_dataclass_inheritance(self) -> None:
        out = QueryEventsOutput(summary="s", query="q", items=[])
        assert isinstance(out, ToolOutput)


# ── 工具集契约测试 ───────────────────────────────────────────────


class TestToolSetContract:
    """验证工具集契约：8 个工具、名称、描述、参数 schema"""

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
                "long_earn.master_agent.create_agent",
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
            ctx.connector = None
            ctx.realtime_provider = None
            ctx.require_llm.return_value.get_llm.return_value = MagicMock()
            return MasterAgent(ctx)

    def test_eight_tools_defined(self, mock_master_agent: MasterAgent) -> None:
        """验证 8 个分层工具全部定义"""
        tools = mock_master_agent._build_tools()
        assert len(tools) == 8

    def test_tool_names(self, mock_master_agent: MasterAgent) -> None:
        """验证 query_*/run_* 两组工具名称"""
        tools = mock_master_agent._build_tools()
        names = [t.name for t in tools]
        for expected in (
            "query_events",
            "query_ontology",
            "query_market",
            "query_memory",
            "web_search",
            "run_research",
            "run_stock_analysis",
            "run_event_collection",
        ):
            assert expected in names
        # ADR-024 §C：旧工具退役
        for retired in ("summarize", "infer_events", "research_strategy"):
            assert retired not in names

    def test_query_prefix_read_only(self, mock_master_agent: MasterAgent) -> None:
        """query_* 组工具描述须声明只读契约"""
        tools = mock_master_agent._build_tools()
        for t in tools:
            if t.name.startswith("query_"):
                assert "只读" in t.description, f"{t.name} 描述缺少只读契约"

    def test_tool_descriptions_non_empty(self, mock_master_agent: MasterAgent) -> None:
        """验证每个工具都有非空描述"""
        tools = mock_master_agent._build_tools()
        for tool in tools:
            assert tool.description, f"工具 {tool.name} 描述为空"

    def test_run_research_params(self, mock_master_agent: MasterAgent) -> None:
        """验证 run_research 工具参数"""
        tools = mock_master_agent._build_tools()
        rr = next(t for t in tools if t.name == "run_research")
        properties = rr.args_schema.model_json_schema().get("properties", {})
        assert "idea" in properties
        assert "constraints" in properties
        assert "default" in properties["constraints"]

    def test_query_events_params(self, mock_master_agent: MasterAgent) -> None:
        """验证 query_events 工具参数"""
        tools = mock_master_agent._build_tools()
        qe = next(t for t in tools if t.name == "query_events")
        properties = qe.args_schema.model_json_schema().get("properties", {})
        assert "query" in properties
        assert "k" in properties
        assert "default" in properties["k"]

    def test_react_agent_compiled(self, mock_master_agent: MasterAgent) -> None:
        """验证 ReAct agent 已编译"""
        assert mock_master_agent._agent is not None


# ── query_* 工具执行测试 ─────────────────────────────────────────


class TestQueryEventsTool:
    """query_events：Substance 事件激活检索"""

    def test_returns_structured_output(self) -> None:
        ctx = _make_context()
        ctx.memory.activate_events.return_value = ["【事件】茅台提价 利好"]
        tool = _tool_by_name(_build_tools(ctx), "query_events")
        out = tool.invoke({"query": "茅台", "k": 5})
        assert "1 条" in out
        payload = _payload(out)
        assert payload["items"] == ["【事件】茅台提价 利好"]
        ctx.memory.activate_events.assert_called_once_with("茅台", k=5)

    def test_empty_activation(self) -> None:
        ctx = _make_context()
        ctx.memory.activate_events.return_value = []
        tool = _tool_by_name(_build_tools(ctx), "query_events")
        out = tool.invoke({"query": "不存在的主题"})
        assert "未激活到相关事件" in out

    def test_failure_is_structured(self) -> None:
        ctx = _make_context()
        ctx.memory.activate_events.side_effect = RuntimeError("store 不可用")
        tool = _tool_by_name(_build_tools(ctx), "query_events")
        out = tool.invoke({"query": "茅台"})
        assert "执行失败" in out and "store 不可用" in out


class TestQueryOntologyTool:
    """query_ontology：Connector.get_concept 薄封装"""

    def test_connector_unavailable(self) -> None:
        ctx = _make_context()
        ctx.connector = None
        tool = _tool_by_name(_build_tools(ctx), "query_ontology")
        out = tool.invoke({"subject": "roe"})
        payload = _payload(out)
        assert payload["resolution_kind"] == "unavailable"

    def test_happy_path(self) -> None:
        ctx = _make_context()
        ctx.connector = MagicMock()
        ctx.connector.get_concept.return_value = ConceptResult(
            concept="roe",
            subject="roe",
            data={"roe": 0.15},
            provenance=["xtquant"],
            related_nodes=[
                OntologyNode(
                    sid="indicator:roe",
                    domain="indicator",
                    label="净资产收益率",
                ),
            ],
            paths=[],
            resolution=ConceptResolution(kind="indicator_panel", payload={}),
        )
        tool = _tool_by_name(_build_tools(ctx), "query_ontology")
        out = tool.invoke({"subject": "roe"})
        payload = _payload(out)
        assert payload["resolution_kind"] == "indicator_panel"
        assert payload["data"] == {"roe": 0.15}
        assert payload["related_nodes"][0]["sid"] == "indicator:roe"
        assert payload["provenance"] == ["xtquant"]

    def test_failure_is_structured(self) -> None:
        ctx = _make_context()
        ctx.connector = MagicMock()
        ctx.connector.get_concept.side_effect = RuntimeError("解析失败")
        tool = _tool_by_name(_build_tools(ctx), "query_ontology")
        out = tool.invoke({"subject": "roe"})
        payload = _payload(out)
        assert payload["resolution_kind"] == "error"


class TestQueryMarketTool:
    """query_market：实时行情快照"""

    def test_provider_unavailable(self) -> None:
        ctx = _make_context()
        ctx.realtime_provider = None
        tool = _tool_by_name(_build_tools(ctx), "query_market")
        out = tool.invoke({"symbols": "600519"})
        assert "不可用" in out

    def test_happy_path_computes_change_pct(self) -> None:
        ctx = _make_context()
        provider = MagicMock()
        provider.is_available = True
        provider.get_latest_quote.return_value = {
            "price": 110.0,
            "preClose": 100.0,
            "volume": 12345,
            "time": "2026-09-07 10:00:00",
            "source": "miniqmt",
        }
        ctx.realtime_provider = provider
        tool = _tool_by_name(_build_tools(ctx), "query_market")
        out = tool.invoke({"symbols": "600519"})
        payload = _payload(out)
        assert len(payload["quotes"]) == 1
        quote = payload["quotes"][0]
        assert quote["symbol"] == "600519"
        assert quote["price"] == 110.0
        assert quote["change_pct"] == 10.0

    def test_invalid_symbols(self) -> None:
        ctx = _make_context()
        tool = _tool_by_name(_build_tools(ctx), "query_market")
        out = tool.invoke({"symbols": "  ,  "})
        assert "未提供有效标的" in out


class TestQueryMemoryTool:
    """query_memory：记忆检索"""

    def test_returns_structured_output(self) -> None:
        ctx = _make_context()
        ctx.memory.search.return_value = ["历史经验 1", "历史经验 2"]
        tool = _tool_by_name(_build_tools(ctx), "query_memory")
        out = tool.invoke({"query": "动量策略", "k": 2})
        payload = _payload(out)
        assert payload["results"] == ["历史经验 1", "历史经验 2"]
        assert "2 条" in out

    def test_empty_results(self) -> None:
        ctx = _make_context()
        ctx.memory.search.return_value = []
        tool = _tool_by_name(_build_tools(ctx), "query_memory")
        out = tool.invoke({"query": "未知主题"})
        assert "未检索到" in out


class TestWebSearchTool:
    """web_search：联网检索（外部 Provider，ADR-021 豁免）"""

    def test_returns_structured_output(self) -> None:
        ctx = _make_context()
        with patch(
            "long_earn.master_agent_tools.kimi_web_search",
            return_value=[
                {"title": "央行降息", "content": "LPR 下调 10bp"},
            ],
        ):
            tool = _tool_by_name(_build_tools(ctx), "web_search")
            out = tool.invoke({"query": "降息"})
        payload = _payload(out)
        assert payload["results"][0]["title"] == "央行降息"
        assert "央行降息" in out

    def test_empty_results(self) -> None:
        ctx = _make_context()
        with patch(
            "long_earn.master_agent_tools.kimi_web_search",
            return_value=[],
        ):
            tool = _tool_by_name(_build_tools(ctx), "web_search")
            out = tool.invoke({"query": "不存在"})
        assert "未找到搜索结果" in out


# ── run_* 工具执行测试 ───────────────────────────────────────────


class TestRunResearchTool:
    """run_research：委托 ResearchAgent（ToG）"""

    def test_returns_structured_output(self) -> None:
        ctx = _make_context()
        research_agent = MagicMock()
        research_agent.invoke.return_value = {
            "result": "策略研发完成",
            "strategy_name": "双均线",
            "strategy_yaml": "name: dual_ma",
            "backtest_result": {
                "metrics": {"total_return": 0.25, "sharpe_ratio": 1.5},
            },
        }
        tools = build_master_tools(
            ctx,
            research_agent=research_agent,
            stock_analysis_subgraph=MagicMock(),
            event_inference_subgraph=MagicMock(),
        )
        out = _tool_by_name(tools, "run_research").invoke(
            {"idea": "动量", "constraints": ""}
        )
        payload = _payload(out)
        assert payload["strategy_name"] == "双均线"
        assert payload["strategy_yaml"] == "name: dual_ma"
        assert payload["metrics"]["sharpe_ratio"] == 1.5
        assert "sharpe_ratio: 1.5" in out

    def test_failure_is_structured(self) -> None:
        ctx = _make_context()
        research_agent = MagicMock()
        research_agent.invoke.side_effect = RuntimeError("回测数据缺失")
        tools = build_master_tools(
            ctx,
            research_agent=research_agent,
            stock_analysis_subgraph=MagicMock(),
            event_inference_subgraph=MagicMock(),
        )
        out = _tool_by_name(tools, "run_research").invoke({"idea": "动量"})
        assert "策略研发执行失败" in out and "回测数据缺失" in out


class TestRunStockAnalysisTool:
    """run_stock_analysis：五视角子图委托"""

    def test_returns_structured_output(self) -> None:
        ctx = _make_context()
        subgraph = MagicMock()
        subgraph.invoke.return_value = {"summary": "茅台基本面强劲"}
        tools = build_master_tools(
            ctx,
            research_agent=MagicMock(),
            stock_analysis_subgraph=subgraph,
            event_inference_subgraph=MagicMock(),
        )
        out = _tool_by_name(tools, "run_stock_analysis").invoke(
            {"query": "分析茅台", "symbols": "600519"}
        )
        payload = _payload(out)
        assert payload["analysis"] == "茅台基本面强劲"
        subgraph.invoke.assert_called_once_with({"query": "分析茅台 (股票: 600519)"})

    def test_error_state_is_structured(self) -> None:
        ctx = _make_context()
        subgraph = MagicMock()
        subgraph.invoke.return_value = {"error": "数据缺失"}
        tools = build_master_tools(
            ctx,
            research_agent=MagicMock(),
            stock_analysis_subgraph=subgraph,
            event_inference_subgraph=MagicMock(),
        )
        out = _tool_by_name(tools, "run_stock_analysis").invoke({"query": "分析茅台"})
        payload = _payload(out)
        assert payload["analysis"] == "数据缺失"


class TestRunEventCollectionTool:
    """run_event_collection：事件采集与推理子图委托"""

    def test_full_pipeline_structured_output(self) -> None:
        ctx = _make_context()
        subgraph = MagicMock()
        subgraph.invoke.return_value = {
            "collected_items": [{"title": "茅台提价"}],
            "extracted_events": [
                {
                    "content": "茅台上调出厂价",
                    "symbols": ["600519"],
                    "sentiment": "positive",
                    "category": "pricing",
                    "confidence": 0.9,
                },
            ],
            "propagated_relations": [
                {"event_index": 0, "target": "600519"},
            ],
            "saved_sids": ["s1", "s2"],
            "summary": {"event_count": 1, "relation_count": 1},
        }
        tools = build_master_tools(
            ctx,
            research_agent=MagicMock(),
            stock_analysis_subgraph=MagicMock(),
            event_inference_subgraph=subgraph,
        )
        out = _tool_by_name(tools, "run_event_collection").invoke(
            {"query": "茅台 新闻"}
        )
        payload = _payload(out)
        assert payload["collected_count"] == 1
        assert payload["event_count"] == 1
        assert payload["relation_count"] == 1
        assert payload["saved_count"] == 2
        assert payload["events"][0]["content"] == "茅台上调出厂价"
        assert "素材 1 条" in out

    def test_empty_collection_short_circuit(self) -> None:
        ctx = _make_context()
        subgraph = MagicMock()
        subgraph.invoke.return_value = {"collected_items": []}
        tools = build_master_tools(
            ctx,
            research_agent=MagicMock(),
            stock_analysis_subgraph=MagicMock(),
            event_inference_subgraph=subgraph,
        )
        out = _tool_by_name(tools, "run_event_collection").invoke({"query": "冷门主题"})
        assert "未采集到相关素材" in out
        payload = _payload(out)
        assert payload["event_count"] == 0


# ── 会话主循环测试（ADR-024 §A）──────────────────────────────────


def _thread_messages(agent: MasterAgent, thread_id: str) -> list[Any]:
    """读取 thread 在 checkpointer 中累积的消息历史。"""
    state = agent._agent.get_state({"configurable": {"thread_id": thread_id}})
    return list(state.values.get("messages", []))


class TestSessionLoop:
    """会话主循环契约：thread_id 多轮复用 / thread 隔离 / close_session 摘要沉淀

    走真实 create_agent + MemorySaver 编译路径，仅 mock 重依赖
    （ResearchAgent / 领域子图 / 工具构建）；FakeListChatModel 每次
    agent.invoke 消耗一个响应（无工具 → 单次模型调用），
    close_session 的摘要 LLM 用 ``_stub_summary_llm`` 显式替换。
    """

    @pytest.fixture
    def session_agent(self) -> MasterAgent:
        model = FakeListChatModel(responses=["第一轮回复", "第二轮回复"])
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
                "long_earn.master_agent.build_master_tools",
                return_value=[],
            ),
        ):
            ctx = MagicMock()
            ctx.logger = MagicMock()
            ctx.memory = MagicMock()
            ctx.require_llm.return_value.get_llm.return_value = model
            return MasterAgent(ctx)

    @staticmethod
    def _stub_summary_llm(agent: MasterAgent, behavior: Any) -> None:
        """替换 close_session 摘要 LLM（返回文本或抛异常）。"""
        mock_llm = MagicMock()
        if isinstance(behavior, Exception):
            mock_llm.invoke.side_effect = behavior
        else:
            mock_llm.invoke.return_value = MagicMock(content=behavior)
        agent._llm = mock_llm

    def test_multi_turn_same_thread_accumulates(
        self, session_agent: MasterAgent
    ) -> None:
        """同一 thread_id 连续 invoke：消息历史累积（2 轮 → 4 条）"""
        session_agent.invoke("第一问", thread_id="t1")
        result = session_agent.invoke("第二问", thread_id="t1")
        assert result["summary"] == "第二轮回复"
        messages = _thread_messages(session_agent, "t1")
        assert len(messages) == 4
        assert messages[0].content == "第一问"
        assert messages[2].content == "第二问"

    def test_threads_isolated(self, session_agent: MasterAgent) -> None:
        """不同 thread_id 的会话历史互不可见"""
        session_agent.invoke("问题A", thread_id="ta")
        session_agent.invoke("问题B", thread_id="tb")
        assert len(_thread_messages(session_agent, "ta")) == 2
        assert len(_thread_messages(session_agent, "tb")) == 2
        assert _thread_messages(session_agent, "ta")[0].content == "问题A"

    def test_invoke_passes_thread_id_config(self, session_agent: MasterAgent) -> None:
        """invoke 的 config 须携带 configurable.thread_id（ADR-024 §A）"""
        with patch.object(session_agent, "_agent", MagicMock()) as mock_agent:
            mock_agent.invoke.return_value = {"messages": []}
            session_agent.invoke("查询", thread_id="t9")
            config = mock_agent.invoke.call_args.kwargs["config"]
            assert config["configurable"]["thread_id"] == "t9"
            assert config["recursion_limit"] > 0

    def test_close_session_persists_summary(self, session_agent: MasterAgent) -> None:
        """close_session：LLM 摘要 → memory.save_session_summary → 清理 thread"""
        session_agent.invoke("第一问", thread_id="t1")
        self._stub_summary_llm(session_agent, "摘要文本")
        closed = session_agent.close_session("t1")
        assert closed["summary"] == "摘要文本"
        assert closed["turns"] == 1
        session_agent.context.memory.save_session_summary.assert_called_once_with(
            thread_id="t1", summary="摘要文本", turns=1
        )
        # thread 状态已清理：checkpointer 中不再有历史
        assert not _thread_messages(session_agent, "t1")

    def test_close_session_counts_turns(self, session_agent: MasterAgent) -> None:
        """turns 按 HumanMessage 数计（含工具中间态时只数用户消息）"""
        session_agent.invoke("第一问", thread_id="t1")
        session_agent.invoke("第二问", thread_id="t1")
        self._stub_summary_llm(session_agent, "摘要文本")
        closed = session_agent.close_session("t1")
        assert closed["turns"] == 2

    def test_close_session_empty_history_skips(
        self, session_agent: MasterAgent
    ) -> None:
        """空会话 close：不写记忆、不报错"""
        closed = session_agent.close_session("never-used")
        assert closed == {"summary": "", "substance_id": "", "turns": 0}
        session_agent.context.memory.save_session_summary.assert_not_called()

    def test_close_session_llm_failure_falls_back(
        self, session_agent: MasterAgent
    ) -> None:
        """摘要 LLM 失败时回退最终 AI 回复，不静默吞异常"""
        session_agent.invoke("第一问", thread_id="t1")
        self._stub_summary_llm(session_agent, RuntimeError("llm 不可用"))
        closed = session_agent.close_session("t1")
        assert closed["summary"] == "第一轮回复"
        assert closed["turns"] == 1
        session_agent.context.memory.save_session_summary.assert_called_once_with(
            thread_id="t1", summary="第一轮回复", turns=1
        )


class TestSaveSessionSummary:
    """MemoryServiceImpl.save_session_summary：KNOWLEDGE 形态沉淀契约"""

    def test_saves_knowledge_substance(self) -> None:
        from long_earn.services.memory_service import MemoryServiceImpl

        impl = MemoryServiceImpl(
            config=MagicMock(), logger=MagicMock(), ontology_graph=None
        )
        with patch.object(impl, "_store") as mock_store:
            mock_store.add.return_value = "sid-1"
            sid = impl.save_session_summary(
                thread_id="t1", summary="用户关注茅台基本面", turns=3
            )
        assert sid == "sid-1"
        s = mock_store.add.call_args.args[0]
        assert s.metadata["experience_type"] == "session_summary"
        assert s.metadata["category"] == "会话摘要"
        assert s.metadata["term"] == "t1"
        assert s.metadata["turns"] == 3
        assert s.content == "用户关注茅台基本面"
        assert s.keys == ["t1"]
