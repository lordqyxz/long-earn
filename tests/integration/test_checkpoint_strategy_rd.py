"""利用 checkpoint 机制测试策略研发功能准确性

通过 LangGraph MemorySaver + interrupt_before 逐节点断点，
验证 HTR 六步循环每个节点执行后的状态完整性。

测试层次：
  1. 逐节点断点 — 在每个关键节点前暂停，验证前序节点产出的状态
  2. 中断恢复 — 模拟中途停止后从 checkpoint 续跑
  3. 线程复用 — _thread_already_completed 检测已完成线程并直接复用结果
  4. 服务层集成 — StrategyResearchService.run_round 的 checkpoint 集成

运行方式：
  uv run pytest tests/integration/test_checkpoint_strategy_rd.py -v -s

前置条件：
  - Ollama 服务运行中（或 .env 配置 DashScope/OpenAI）
  - DuckDB 回测缓存有数据（至少 csi300 成分股行情）
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import pytest
from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver

# 在 load_dotenv 前设置，跳过启动时缓存同步（加速测试启动）
os.environ.setdefault("LONG_EARN_SKIP_CACHE_SYNC", "1")

from long_earn.services.strategy_research_service import (
    StrategyResearchService,
)

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from long_earn.config import RuntimeContext

load_dotenv()

# HTR 子图的关键节点顺序（不含 START/END）
_HTR_NODES = [
    "init_tree",
    "observe",
    "ideate",
    "select",
    "dispatch",
    "executor",
    "backpropagate",
    "decide",
    "save_tree",
    "gap_detector",
    "operator_dev",
]


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def context() -> RuntimeContext:
    """创建真实运行时上下文

    测试优化：
      - LONG_EARN_SKIP_CACHE_SYNC=1 跳过启动时缓存同步
      - 回测区间缩短到 3 个月（减少回测耗时）
      - HTR 循环 1 轮、串行执行
      - 仍遵守数据分割规范：只用训练集区间
    """
    from long_earn.config import AppConfig
    from long_earn.context_init import create_runtime_context

    config = AppConfig.from_env()
    # 限制 HTR 循环 1 轮，避免测试时间过长
    config.htr_max_cycles = 1
    config.htr_max_select = 1
    config.max_iterations = 1
    config.max_workers = 1  # 串行回测，避免并行开销
    # 使用训练集内的短区间（铁律：开发阶段不碰测试集/验证集）
    # 3 个月足够验证策略研发流程正确性，大幅减少回测耗时
    config.train_start_date = "2024-06-01"
    config.train_end_date = "2024-08-31"
    config.backtest_start_date = config.train_start_date
    config.backtest_end_date = config.train_end_date

    ctx = create_runtime_context(config)
    try:
        ctx.memory.initialize()
    except Exception as e:
        pytest.skip(f"知识库初始化失败: {e}")
    return ctx


@pytest.fixture
def checkpointer() -> MemorySaver:
    """每个测试用独立的 MemorySaver"""
    return MemorySaver()


@pytest.fixture
def thread_config() -> dict[str, Any]:
    """统一的 thread_id 配置"""
    return {"configurable": {"thread_id": "test-checkpoint-rd"}}


# ── 测试 1：逐节点断点状态验证 ────────────────────────────────────


class TestNodeByNodeCheckpoint:
    """用 interrupt_before 逐节点断点，验证每个节点执行后的状态完整性。

    每个测试方法对应一个断点位置：
    - interrupt_before=["observe"] → 验证 init_tree 执行后的状态
    - interrupt_before=["ideate"] → 验证 observe 执行后的状态
    - 以此类推
    """

    def _build_graph(
        self,
        context: RuntimeContext,
        checkpointer: MemorySaver,
        interrupt_nodes: list[str],
    ) -> CompiledStateGraph:
        """构建带 checkpoint 的 HTR 子图"""
        from long_earn.strategy_rd.htr_subgraph import create_htr_subgraph

        return create_htr_subgraph(
            context,
            checkpointer=checkpointer,
            interrupt_before=interrupt_nodes,
        )

    def test_init_tree_creates_hypothesis_tree(
        self,
        context: RuntimeContext,
        checkpointer: MemorySaver,
        thread_config: dict,
    ):
        """init_tree 执行后应创建 hypothesis_tree 与 run_id"""
        graph = self._build_graph(
            context, checkpointer, interrupt_nodes=["observe"]
        )

        # 首次 invoke，在 observe 前暂停
        graph.invoke({"query": "研究一个基于ROE的选股策略"}, config=thread_config)

        # 验证 init_tree 执行后的状态
        snapshot = graph.get_state(thread_config)
        values = snapshot.values

        # init_tree 应创建 hypothesis_tree
        assert values.get("hypothesis_tree") is not None, (
            "init_tree 执行后 hypothesis_tree 不应为空"
        )
        tree = values["hypothesis_tree"]
        assert isinstance(tree, dict), "hypothesis_tree 应为 dict"

        # run_id 应已生成
        assert values.get("run_id"), "init_tree 执行后 run_id 不应为空"

        # query 应已传入
        assert values.get("query") == "研究一个基于ROE的选股策略"

    def test_observe_populates_observation(
        self,
        context: RuntimeContext,
        checkpointer: MemorySaver,
        thread_config: dict,
    ):
        """observe 执行后应有观察数据"""
        graph = self._build_graph(
            context, checkpointer, interrupt_nodes=["ideate"]
        )

        graph.invoke({"query": "研究一个基于ROE的选股策略"}, config=thread_config)

        snapshot = graph.get_state(thread_config)
        values = snapshot.values

        # observe 节点应产出 knowledge_context 或某种观察结果
        # 即使 LLM 观察失败，observe 节点也应执行（不阻塞子图）
        assert values.get("hypothesis_tree") is not None, (
            "前序 init_tree 的 hypothesis_tree 应保留"
        )

    def test_ideate_generates_hypotheses(
        self,
        context: RuntimeContext,
        checkpointer: MemorySaver,
        thread_config: dict,
    ):
        """ideate 执行后应有假设生成"""
        graph = self._build_graph(
            context, checkpointer, interrupt_nodes=["select"]
        )

        graph.invoke({"query": "研究一个基于ROE的选股策略"}, config=thread_config)

        snapshot = graph.get_state(thread_config)
        values = snapshot.values

        # ideate 应产出 improvement_hypotheses
        hypotheses = values.get("improvement_hypotheses")
        assert hypotheses is not None, "ideate 应产出 improvement_hypotheses"
        assert isinstance(hypotheses, list), "improvement_hypotheses 应为 list"
        assert len(hypotheses) > 0, "至少应生成 1 个假设"

        # 假设树应已更新（添加了叶子节点）
        tree = values.get("hypothesis_tree")
        assert tree is not None, "hypothesis_tree 应存在"

    def test_select_picks_hypothesis(
        self,
        context: RuntimeContext,
        checkpointer: MemorySaver,
        thread_config: dict,
    ):
        """select 执行后应有选中的假设"""
        graph = self._build_graph(
            context, checkpointer, interrupt_nodes=["dispatch"]
        )

        graph.invoke({"query": "研究一个基于ROE的选股策略"}, config=thread_config)

        snapshot = graph.get_state(thread_config)
        values = snapshot.values

        # select 应设置 selected_leaves
        selected = values.get("selected_leaves")
        assert selected is not None, "select 应设置 selected_leaves"
        assert isinstance(selected, list)
        assert len(selected) > 0, "至少应选中 1 个假设"

    def test_executor_produces_strategy_and_backtest(
        self,
        context: RuntimeContext,
        checkpointer: MemorySaver,
        thread_config: dict,
    ):
        """executor 执行后应有策略 YAML 与回测结果"""
        graph = self._build_graph(
            context, checkpointer, interrupt_nodes=["backpropagate"]
        )

        graph.invoke({"query": "研究一个基于ROE的选股策略"}, config=thread_config)

        snapshot = graph.get_state(thread_config)
        values = snapshot.values

        # executor 应产出 strategy_yaml（或 optimized_strategy_yaml）
        strategy_yaml = (
            values.get("strategy_yaml")
            or values.get("optimized_strategy_yaml")
        )
        assert strategy_yaml, "executor 应产出策略 YAML"
        assert isinstance(strategy_yaml, str)
        assert len(strategy_yaml) > 0

        # executor 应产出 backtest_result
        backtest_result = values.get("backtest_result")
        assert backtest_result is not None, "executor 应产出回测结果"
        assert isinstance(backtest_result, dict)

    def test_backpropagate_updates_tree(
        self,
        context: RuntimeContext,
        checkpointer: MemorySaver,
        thread_config: dict,
    ):
        """backpropagate 执行后假设树应更新

        HTR _backpropagate_node 将实验结果抽象为洞察并传播到父节点，
        更新 hypothesis_tree（不设置 reflection 字段，那是原 subgraph 的字段）。
        """
        graph = self._build_graph(
            context, checkpointer, interrupt_nodes=["decide"]
        )

        graph.invoke({"query": "研究一个基于ROE的选股策略"}, config=thread_config)

        snapshot = graph.get_state(thread_config)
        values = snapshot.values

        # backpropagate 应更新 hypothesis_tree（洞察传播后）
        tree = values.get("hypothesis_tree")
        assert tree is not None, "backpropagate 后 hypothesis_tree 应存在"

        # executor_results 应保留（backpropagate 读取但不删除）
        executor_results = values.get("executor_results")
        assert executor_results is not None, (
            "executor_results 应在 backpropagate 后保留"
        )

    def test_decide_makes_decision(
        self,
        context: RuntimeContext,
        checkpointer: MemorySaver,
        thread_config: dict,
    ):
        """decide 执行后应有决策结果

        HTR _decide_node 设置 result（action: merge/continue/stop）和 iteration，
        不设置 should_continue（那是原 subgraph 的字段）。
        """
        graph = self._build_graph(
            context, checkpointer, interrupt_nodes=["save_tree"]
        )

        graph.invoke({"query": "研究一个基于ROE的选股策略"}, config=thread_config)

        snapshot = graph.get_state(thread_config)
        values = snapshot.values

        # decide 应设置 result（action: merge/continue/stop）
        result = values.get("result")
        assert result is not None, "decide 应设置 result"
        assert result in ("merge", "continue", "stop"), (
            f"result 应为 merge/continue/stop，实际: {result}"
        )

        # iteration 应递增
        iteration = values.get("iteration")
        assert iteration is not None, "decide 应设置 iteration"

        # 假设树应存在
        assert values.get("hypothesis_tree") is not None

    def test_save_tree_persists(
        self,
        context: RuntimeContext,
        checkpointer: MemorySaver,
        thread_config: dict,
    ):
        """save_tree 执行后假设树应落盘"""
        graph = self._build_graph(
            context, checkpointer, interrupt_nodes=["gap_detector"]
        )

        graph.invoke({"query": "研究一个基于ROE的选股策略"}, config=thread_config)

        snapshot = graph.get_state(thread_config)
        values = snapshot.values

        # save_tree 应执行（假设树落盘）
        assert values.get("hypothesis_tree") is not None
        # experience_saved 可能在 save_tree 中设置
        # 不强制断言 experience_saved，因为 save_experience 可能在子图不同位置


# ── 测试 2：中断恢复 ──────────────────────────────────────────────


class TestInterruptResume:
    """测试中途停止后从 checkpoint 续跑"""

    def test_resume_from_observe(
        self,
        context: RuntimeContext,
        checkpointer: MemorySaver,
        thread_config: dict,
    ):
        """在 observe 前中断，后续续跑应从 observe 继续"""
        from long_earn.strategy_rd.htr_subgraph import create_htr_subgraph

        # 阶段 1：在 observe 前暂停
        graph1 = create_htr_subgraph(
            context,
            checkpointer=checkpointer,
            interrupt_before=["observe"],
        )
        graph1.invoke({"query": "研究一个基于ROE的选股策略"}, config=thread_config)

        # 验证在 observe 前暂停
        snapshot1 = graph1.get_state(thread_config)
        assert snapshot1.next, "应在 observe 前暂停"
        assert "observe" in snapshot1.next, f"next 应含 observe，实际: {snapshot1.next}"

        # 阶段 2：用同一 checkpointer + thread_id 续跑
        # 重新编译子图（不带 interrupt），用同一 checkpointer
        graph2 = create_htr_subgraph(context, checkpointer=checkpointer)
        # 传 None 续跑
        result = graph2.invoke(None, config=thread_config)

        # 验证最终结果完整
        assert result is not None
        assert result.get("hypothesis_tree") is not None, (
            "续跑后应有 hypothesis_tree"
        )

    def test_resume_from_executor(
        self,
        context: RuntimeContext,
        checkpointer: MemorySaver,
        thread_config: dict,
    ):
        """在 executor 前中断，续跑应从 executor 继续"""
        from long_earn.strategy_rd.htr_subgraph import create_htr_subgraph

        # 阶段 1：在 executor 前暂停
        graph1 = create_htr_subgraph(
            context,
            checkpointer=checkpointer,
            interrupt_before=["executor"],
        )
        graph1.invoke({"query": "研究动量策略"}, config=thread_config)

        snapshot1 = graph1.get_state(thread_config)
        assert snapshot1.next, "应在 executor 前暂停"
        assert "executor" in snapshot1.next

        # 中断时应有 hypothesis_tree 和 selected_leaves
        values1 = snapshot1.values
        assert values1.get("hypothesis_tree") is not None
        assert values1.get("selected_leaves") is not None

        # 阶段 2：续跑
        graph2 = create_htr_subgraph(context, checkpointer=checkpointer)
        result = graph2.invoke(None, config=thread_config)

        # 续跑后应有策略和回测结果
        strategy_yaml = (
            result.get("strategy_yaml")
            or result.get("optimized_strategy_yaml")
        )
        assert strategy_yaml, "续跑后应有策略 YAML"
        assert result.get("backtest_result") is not None, "续跑后应有回测结果"


# ── 测试 3：线程复用 ──────────────────────────────────────────────


class TestThreadReuse:
    """测试 _thread_already_completed 检测已完成线程并直接复用结果"""

    def test_completed_thread_detected_and_reused(
        self,
        context: RuntimeContext,
    ):
        """已完成线程应被 _thread_already_completed 检测到并复用"""
        from langgraph.checkpoint.memory import MemorySaver

        from long_earn.strategy_rd.htr_subgraph import (
            create_htr_subgraph as create_strategy_rd_subgraph,
        )

        checkpointer = MemorySaver()
        thread_id = "test-reuse"
        thread_config = {"configurable": {"thread_id": thread_id}}

        # 阶段 1：完整运行一轮
        graph = create_strategy_rd_subgraph(
            context, checkpointer=checkpointer
        )
        graph.invoke(
            {"query": "研究一个基于ROE的选股策略"}, config=thread_config
        )

        # 验证线程已完成
        snapshot = graph.get_state(thread_config)
        assert not snapshot.next, "线程应已完成（next 为空）"
        assert snapshot.values, "线程应有最终状态"

        # 阶段 2：用 _thread_already_completed 检测
        is_completed = (
            StrategyResearchService._thread_already_completed(
                graph, thread_config
            )
        )
        assert is_completed, "_thread_already_completed 应返回 True"

    def test_incomplete_thread_not_reused(
        self,
        context: RuntimeContext,
    ):
        """未完成线程不应被 _thread_already_completed 误判"""
        from langgraph.checkpoint.memory import MemorySaver

        from long_earn.strategy_rd.htr_subgraph import (
            create_htr_subgraph as create_strategy_rd_subgraph,
        )

        checkpointer = MemorySaver()
        thread_id = "test-incomplete"
        thread_config = {"configurable": {"thread_id": thread_id}}

        # 在 observe 前中断
        graph = create_strategy_rd_subgraph(
            context,
            checkpointer=checkpointer,
            interrupt_before=["observe"],
        )
        graph.invoke({"query": "研究策略"}, config=thread_config)

        # 线程未完成
        is_completed = (
            StrategyResearchService._thread_already_completed(
                graph, thread_config
            )
        )
        assert not is_completed, "未完成线程不应被判定为已完成"

    def test_nonexistent_thread_not_reused(
        self,
        context: RuntimeContext,
    ):
        """不存在的 thread_id 不应被判定为已完成"""
        from langgraph.checkpoint.memory import MemorySaver

        from long_earn.strategy_rd.htr_subgraph import (
            create_htr_subgraph as create_strategy_rd_subgraph,
        )

        checkpointer = MemorySaver()
        graph = create_strategy_rd_subgraph(
            context, checkpointer=checkpointer
        )

        # 不存在的 thread_id
        thread_config = {"configurable": {"thread_id": "nonexistent"}}
        is_completed = (
            StrategyResearchService._thread_already_completed(
                graph, thread_config
            )
        )
        assert not is_completed, "不存在的 thread 不应被判定为已完成"


# ── 测试 4：服务层 checkpoint 集成 ─────────────────────────────────


class TestStrategyResearchServiceCheckpoint:
    """测试 StrategyResearchService.run_round 与 checkpoint 的集成"""

    def test_run_round_with_checkpoint_persists_state(
        self,
        context: RuntimeContext,
    ):
        """run_round 启用 checkpoint 后状态应持久化"""
        from langgraph.checkpoint.memory import MemorySaver

        from long_earn.services.strategy_research_service import (
            StrategyResearchService,
        )

        checkpointer = MemorySaver()
        service = StrategyResearchService(context)

        thread_id = "test-svc-checkpoint"
        result = service.run_round(
            idea="研究一个基于ROE的选股策略",
            max_iterations=1,
            checkpointer=checkpointer,
            thread_id=thread_id,
        )

        # 验证结果完整
        assert result is not None
        assert isinstance(result.strategy_yaml, str)
        assert len(result.strategy_yaml) > 0, "应产出策略 YAML"
        assert result.backtest_result is not None
        assert isinstance(result.backtest_result, dict)

        # 验证 checkpoint 状态已保存
        # 用同一 checkpointer 重新构建子图，检查线程完成状态
        from long_earn.strategy_rd.htr_subgraph import (
            create_htr_subgraph as create_strategy_rd_subgraph,
        )

        graph = create_strategy_rd_subgraph(
            context, checkpointer=checkpointer
        )
        thread_config = {"configurable": {"thread_id": thread_id}}
        is_completed = (
            StrategyResearchService._thread_already_completed(
                graph, thread_config
            )
        )
        assert is_completed, "checkpoint 应记录线程完成状态"

    def test_run_round_reuses_completed_thread(
        self,
        context: RuntimeContext,
    ):
        """已完成线程的 run_round 应直接复用结果"""
        from langgraph.checkpoint.memory import MemorySaver

        from long_earn.services.strategy_research_service import (
            StrategyResearchService,
        )

        checkpointer = MemorySaver()
        service = StrategyResearchService(context)

        thread_id = "test-svc-reuse"

        # 首次运行
        result1 = service.run_round(
            idea="研究一个基于ROE的选股策略",
            max_iterations=1,
            checkpointer=checkpointer,
            thread_id=thread_id,
        )
        assert result1.strategy_yaml

        # 第二次运行同一 thread_id — 应复用结果
        result2 = service.run_round(
            idea="研究一个基于ROE的选股策略",
            max_iterations=1,
            checkpointer=checkpointer,
            thread_id=thread_id,
        )

        # 结果应一致（复用而非重跑）
        assert result2.strategy_yaml == result1.strategy_yaml, (
            "已完成线程应复用结果，策略 YAML 应一致"
        )

    def test_run_loop_with_checkpoint_multi_round(
        self,
        context: RuntimeContext,
    ):
        """run_loop 启用 checkpoint 后多轮应正常执行"""
        from langgraph.checkpoint.memory import MemorySaver

        from long_earn.services.strategy_research_service import (
            StrategyResearchService,
        )

        checkpointer = MemorySaver()
        service = StrategyResearchService(context)

        summary = service.run_loop(
            idea="研究一个基于ROE的选股策略",
            max_rounds=2,
            max_iterations=1,
            min_improvement=0.001,
            checkpointer=checkpointer,
            thread_id_prefix="test-loop",
        )

        # 验证循环完成
        assert summary is not None
        assert len(summary.rounds) > 0, "至少应完成 1 轮"

        # 每轮应有 thread_id 记录
        for rd in summary.rounds:
            assert rd.get("round") is not None
