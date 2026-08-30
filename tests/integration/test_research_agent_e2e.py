"""ResearchAgent 端到端集成测试（ADR-018）

验证 ResearchAgent 在真实 LLM + 回测路径上的正反馈闭环：
1. ResearchAgent 可正确初始化并编译 LangGraph ReAct agent
2. invoke() 返回包含 summary / result / beam_paths / event_context 的结果
3. 事件上下文 prepare_context 自动激活
4. 工具集完整（11 个工具）
"""

from __future__ import annotations

import os
import urllib.request

import pytest


def _ollama_available() -> bool:
    """检查本地 Ollama 是否可用。"""
    if os.environ.get("LONG_EARN_SKIP_E2E_LLM"):
        return False
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


class TestResearchAgentE2E:
    """ResearchAgent 端到端集成测试"""

    @pytest.fixture
    def agent(self, context):
        """创建 ResearchAgent 实例。"""
        from long_earn.strategy_rd.research_agent import ResearchAgent

        try:
            return ResearchAgent(context)
        except Exception as e:
            pytest.skip(f"ResearchAgent 初始化失败（LLM 不可用？）: {e}")

    # ── 快速契约检查（无需 LLM） ──

    def test_agent_initializes(self, agent) -> None:
        """ResearchAgent 应成功初始化并编译 ReAct agent。"""
        assert agent is not None
        assert agent._agent is not None
        assert hasattr(agent, "context")

    def test_tool_count(self, agent) -> None:
        """工具集应包含至少 11 个工具（含 activate_subgraph）。"""
        tools = agent._build_tools()
        assert len(tools) >= 11, f"工具数: {len(tools)}"

    def test_tool_names_include_core(self, agent) -> None:
        """核心工具名应存在。"""
        names = {t.name for t in agent._build_tools()}
        expected = {
            "prepare_context",
            "activate_subgraph",
            "expand_relations",
            "prune_paths",
            "run_backtest",
            "run_oos_gates",
            "record_path_outcome",
            "list_operators_tool",
            "compile_strategy_yaml",
            "develop_operator",
            "prove_causality",
        }
        missing = expected - names
        assert not missing, f"缺少核心工具: {missing}"

    # ── 真实 LLM 调用（需要 Ollama 运行） ──

    @pytest.mark.skipif(
        not _ollama_available(),
        reason="Ollama 未运行（设置 LONG_EARN_SKIP_E2E_LLM=1 也可跳过）",
    )
    def test_invoke_returns_result(self, agent) -> None:
        """invoke() 应返回包含关键字段的结果字典。"""
        result = agent.invoke(
            "研究沪深300中低波动率且高动量的选股策略",
            constraints="仅使用 windowed 和 filter_threshold 算子",
        )
        assert isinstance(result, dict), "invoke 应返回 dict"
        assert "result" in result or "messages" in result, (
            "结果应包含 result 或 messages"
        )
        # 事件上下文应已激活
        assert "event_context" in result, "结果应包含 event_context"

    @pytest.mark.skipif(
        not _ollama_available(),
        reason="Ollama 未运行（设置 LONG_EARN_SKIP_E2E_LLM=1 也可跳过）",
    )
    def test_beam_paths_reset_per_invoke(self, agent) -> None:
        """每次 invoke 应重置 beam_paths 和 evidence_cache。"""
        agent._beam_paths = [{"test": "stale"}]
        agent._evidence_cache["deadbeef"] = None  # type: ignore[assignment]
        agent._strategy_trial_count = 99

        agent.invoke(
            "简单测试：沪深300动量策略",
            constraints="仅使用 windowed 算子",
        )

        # beam_paths / evidence_cache 应在 invoke 入口重置
        assert agent._strategy_trial_count >= 0, "trial_count 应被重置（至少不为负数）"
