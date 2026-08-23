"""策略研发子图接口测试"""

import json
from unittest.mock import MagicMock

from long_earn.config import RuntimeContext
from long_earn.services import MemoryService, StrategyExperience
from long_earn.services.backtest_service import BacktestService
from long_earn.services.llm_service import LLMService
from long_earn.services.logger_service import LoggerService
from long_earn.services.monitoring_service import MonitoringService
from long_earn.services.stock_service import StockService


def _make_mock_context() -> RuntimeContext:
    """创建带 mock 服务的 RuntimeContext"""
    mock_llm = MagicMock(spec=LLMService)
    mock_response = MagicMock()
    mock_response.content = "test response"
    mock_llm.invoke.return_value = mock_response

    mock_memory = MagicMock(spec=MemoryService)
    mock_memory.search.return_value = ["test knowledge"]
    mock_memory.save_experience.return_value = "test-exp-id"

    mock_config = MagicMock()
    mock_config.llm_type = "ollama"
    mock_config.llm_model = "test"
    mock_config.llm_base_url = "http://localhost"
    mock_config.init_dir = "./init"
    mock_config.max_iterations = 1
    mock_config.backtest_start_date = "2020-01-01"
    mock_config.backtest_end_date = "2023-12-31"

    return RuntimeContext(
        llm_service=mock_llm,
        memory=mock_memory,
        stock_service=MagicMock(spec=StockService),
        backtest_service=MagicMock(spec=BacktestService),
        logger=MagicMock(spec=LoggerService),
        monitoring=MagicMock(spec=MonitoringService),
        config=mock_config,
    )


class TestPromptModuleImports:
    def test_create_strategy_research_prompt_returns_string(self):
        from long_earn.strategy_rd.agents.strategy_research_prompt import (
            create_strategy_research_prompt,
        )

        result = create_strategy_research_prompt(
            target_market="stock",
            query="test query",
            strategy_examples="none",
            strategy_context="none",
        )
        assert isinstance(result, str)
        assert len(result) > 0


class TestBranchReflection:
    def test_run_branch_reflection_calls_llm_service(self):
        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        context = _make_mock_context()
        agent = StrategyResearchAgent(context=context)

        branch_result = {
            "direction": "收益增强",
            "reflection": "Returns are low",
            "improvement_suggestions": [
                {"priority": "high", "issue": "low return", "suggestion": "add factors"}
            ],
        }
        mock_response = MagicMock()
        mock_response.content = json.dumps(branch_result)
        context.llm_service.invoke.return_value = mock_response

        result = agent._run_branch_reflection(
            direction="收益增强",
            strategy={"description": "test strategy"},
            backtest_result={"metrics": {"return": 5}},
        )

        assert context.llm_service.invoke.called
        assert result["direction"] == "收益增强"
        assert "reflection" in result


class TestEvolutionLineage:
    """多轮演进与记忆系统交互测试

    保证 supervisor 决定继续迭代后，optimize 阶段：
    - 起点是上一轮 optimized_strategy（演进真正在累积）
    - 当前回测指标进入 prompt（不再是硬编码 "无"）
    - 历史经验从 memory.search_experience 拉取并注入
    - optimized_strategy 带 evolution_lineage 谱系字段
    """

    def test_optimize_uses_previous_optimized_as_base(self):
        """optimize_strategy 收到 previous_backtest 时，
        prompt 的 backtest_history 应包含真实指标，非 '无'。"""
        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        context = _make_mock_context()
        agent = StrategyResearchAgent(context=context)
        # search_experience 返回非空 → 验证记忆注入路径
        context.memory.search_experience = MagicMock(
            return_value=[
                StrategyExperience(
                    name="OldStrategy",
                    code="...",
                    rationale="...",
                    metrics={"sharpe_ratio": 1.5, "total_return": 0.3},
                )
            ]
        )
        mock_response = MagicMock()
        mock_response.content = "optimized desc"
        context.llm_service.invoke.return_value = mock_response

        previous_backtest = {
            "total_return": 0.123,
            "sharpe_ratio": 0.45,
            "max_drawdown": -0.18,
            "trading_days": 250,
        }
        result = agent.optimize_strategy(
            strategy={"strategy_name": "TestS", "factors_used": ["roe"]},
            improvement_suggestions=["降低回撤"],
            previous_backtest=previous_backtest,
        )

        # LLM 被调用了一次，捕获 prompt 文本
        called_prompt = context.llm_service.invoke.call_args[0][0]
        assert "0.123" in called_prompt  # 真实回测数值进入 prompt
        assert "OldStrategy" in called_prompt  # 历史经验进入 prompt
        # evolution_lineage 谱系记录
        assert result.get("optimized") is True
        assert isinstance(result.get("evolution_lineage"), list)
        assert len(result["evolution_lineage"]) == 1
        assert result["evolution_lineage"][0]["had_backtest"] is True

    def test_optimize_marks_unreliable_metrics(self):
        """previous_backtest 带 metrics_unreliable 时，
        prompt 不应把占位 0 当真实指标，而要给出明确警告。"""
        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        context = _make_mock_context()
        context.memory.search_experience = MagicMock(return_value=[])
        agent = StrategyResearchAgent(context=context)
        mock_response = MagicMock()
        mock_response.content = "optimized desc"
        context.llm_service.invoke.return_value = mock_response

        agent.optimize_strategy(
            strategy={"strategy_name": "TestS"},
            improvement_suggestions=["a"],
            previous_backtest={
                "total_return": 0,
                "sharpe_ratio": 0,
                "metrics_unreliable": True,
                "error": "数据不足",
            },
        )

        called_prompt = context.llm_service.invoke.call_args[0][0]
        assert "数据不足" in called_prompt or "占位" in called_prompt


class TestToTBranchScoring:
    """ToT 多分支评分必须能从扁平 backtest_result 读出真实指标，
    否则所有分支都拿默认 +5 分，sorted 稳定排序让 best_branch 永远是
    OPTIMIZATION_DIRECTIONS 第一个键 —— 多分支退化为单一分支。
    """

    def _agent(self):
        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        context = _make_mock_context()
        return StrategyResearchAgent(context=context)

    def _branches(self):
        return [
            {"direction": "收益增强", "reflection": "", "improvement_suggestions": []},
            {"direction": "风险控制", "reflection": "", "improvement_suggestions": []},
            {
                "direction": "收益稳定性",
                "reflection": "",
                "improvement_suggestions": [],
            },
        ]

    def test_high_drawdown_makes_risk_control_top(self):
        """max_drawdown 极大时（扁平结构）"风险控制"分支应得分最高"""
        agent = self._agent()
        backtest_result = {
            "total_return": 0.12,
            "sharpe_ratio": 0.6,
            "max_drawdown": -0.40,  # 远超 _DRAWDOWN_RISK_THRESHOLD 0.30
        }

        evaluated = agent._evaluate_branches(self._branches(), backtest_result)

        assert evaluated[0]["direction"] == "风险控制"
        assert evaluated[0]["score"] == 30

    def test_low_sharpe_makes_stability_top(self):
        """sharpe 极差但 drawdown 轻微时"收益稳定性"应得分最高"""
        agent = self._agent()
        backtest_result = {
            "total_return": 0.12,  # 跑赢阈值，收益增强 +5
            "sharpe_ratio": 0.1,  # 远低 _POOR_SHARPE_THRESHOLD 0.3
            "max_drawdown": -0.05,  # 远低 _DRAWDOWN_MODERATE_THRESHOLD 0.20
        }

        evaluated = agent._evaluate_branches(self._branches(), backtest_result)

        assert evaluated[0]["direction"] == "收益稳定性"
        assert evaluated[0]["score"] == 30

    def test_negative_return_makes_yield_top(self):
        """收益为负时"收益增强"应得分最高"""
        agent = self._agent()
        backtest_result = {
            "total_return": -0.15,
            "sharpe_ratio": 0.8,  # 中等
            "max_drawdown": -0.10,  # 轻微
        }

        evaluated = agent._evaluate_branches(self._branches(), backtest_result)

        assert evaluated[0]["direction"] == "收益增强"
        assert evaluated[0]["score"] == 30

    def test_nested_metrics_still_works(self):
        """旧嵌套结构（_backtest_node engine_error 占位）必须仍能正确评分"""
        agent = self._agent()
        backtest_result = {
            "metrics": {
                "annual_return": -0.20,
                "sharpe_ratio": 1.2,
                "max_drawdown": -0.05,
            }
        }

        evaluated = agent._evaluate_branches(self._branches(), backtest_result)
        assert evaluated[0]["direction"] == "收益增强"

    def test_flat_overrides_nested_when_both_present(self):
        """扁平字段优先于嵌套——同一字段两边给值时取扁平"""
        agent = self._agent()
        backtest_result = {
            "max_drawdown": -0.50,  # 扁平：极大
            "metrics": {"max_drawdown": -0.05},  # 嵌套：轻微
        }

        evaluated = agent._evaluate_branches(self._branches(), backtest_result)
        # 扁平的 -0.50 应让风险控制得到 +30
        risk = next(b for b in evaluated if b["direction"] == "风险控制")
        assert risk["score"] == 30


class TestReflectionFallbackFlatFields:
    """reflect 兜底路径必须能从扁平 backtest_result 读出真实指标，
    而不是因为没找到嵌套 metrics 字段就退化为"无法获取回测指标"。
    """

    def test_simple_fallback_reads_flat_fields(self):
        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        context = _make_mock_context()
        agent = StrategyResearchAgent(context=context)

        result = agent._simple_fallback(
            strategy={"name": "S"},
            backtest_result={
                "total_return": -0.1,
                "sharpe_ratio": 0.2,
                "max_drawdown": -0.35,
            },
        )

        # 必须能拿出真实指标，而不是返回死分支
        assert "无法获取回测指标" not in result["reflection"]
        assert "0.20" in result["reflection"] or "0.2" in result["reflection"]
        # 业绩极差（max_dd > 阈值），至少应给出风控建议
        assert any(
            "止损" in s or "回撤" in s for s in result["improvement_suggestions"]
        )
        # primary_issue 应被填充
        assert "primary_issue" in result

    def test_simple_fallback_returns_dead_branch_only_when_truly_empty(self):
        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        context = _make_mock_context()
        agent = StrategyResearchAgent(context=context)

        # 真正什么指标都没有 → 死分支合理
        result = agent._simple_fallback(strategy={}, backtest_result={"error": "x"})
        assert result["reflection"] == "无法获取回测指标"

    def test_simple_fallback_reads_nested_metrics_too(self):
        """旧嵌套结构的兼容性（_backtest_node 在 engine_error 时填的占位）"""
        from long_earn.strategy_rd.agents.strategy_research_agent import (
            StrategyResearchAgent,
        )

        context = _make_mock_context()
        agent = StrategyResearchAgent(context=context)

        result = agent._simple_fallback(
            strategy={},
            backtest_result={
                "metrics": {
                    "return": 0.05,
                    "sharpe_ratio": 0.6,
                    "max_drawdown": -0.1,
                }
            },
        )
        assert "无法获取回测指标" not in result["reflection"]


class TestSupervisorResilience:
    """监督器多轮演进韧性测试

    防止 LLM 输出格式异常导致系统永远停在第 1 轮。
    """

    def _make_supervisor(
        self, llm_content: str | None = None, raises: Exception | None = None
    ):
        from long_earn.strategy_rd.agents.strategy_rd_supervisor import (
            StrategyRdSupervisor,
        )

        context = _make_mock_context()
        if raises is not None:
            context.llm_service.invoke.side_effect = raises
        else:
            mock_response = MagicMock()
            mock_response.content = llm_content or ""
            context.llm_service.invoke.return_value = mock_response
        return StrategyRdSupervisor(context=context), context

    def test_max_iterations_stops(self):
        """已达 max_iterations 必须停止"""
        sup, ctx = self._make_supervisor(llm_content='{"should_continue": true}')
        assert (
            sup.should_continue(
                iteration=3,
                max_iterations=3,
                strategy={},
                backtest_result={},
                reflection="",
                improvement_suggestions="",
            )
            is False
        )
        # 不应调用 LLM，因为已直接返回
        assert not ctx.llm_service.invoke.called

    def test_invalid_json_does_not_crash_and_defaults_continue(self):
        """LLM 返回非 JSON 字符串时不应崩溃，且默认继续（在迭代预算内）"""
        sup, _ = self._make_supervisor(llm_content="this is not json at all 👻")
        result = sup.should_continue(
            iteration=1,
            max_iterations=3,
            strategy={},
            backtest_result={"sharpe_ratio": 0.2},
            reflection="",
            improvement_suggestions="",
        )
        assert result is True

    def test_explicit_stop_respected(self):
        """LLM 显式 should_continue=False 时停止"""
        sup, _ = self._make_supervisor(
            llm_content='{"should_continue": false, "reason": "ok enough"}'
        )
        assert (
            sup.should_continue(
                iteration=1,
                max_iterations=3,
                strategy={},
                backtest_result={},
                reflection="",
                improvement_suggestions="",
            )
            is False
        )

    def test_missing_should_continue_uses_sharpe_fallback(self):
        """LLM 没返回 should_continue 字段，业绩好(sharpe>=1.5)则停止，否则继续"""
        sup_good, _ = self._make_supervisor(llm_content='{"reason": "no field"}')
        # 业绩明显达标 → 停止
        assert (
            sup_good.should_continue(
                iteration=1,
                max_iterations=3,
                strategy={},
                backtest_result={"sharpe_ratio": 2.0},
                reflection="",
                improvement_suggestions="",
            )
            is False
        )

        sup_poor, _ = self._make_supervisor(llm_content='{"reason": "no field"}')
        # 业绩不达标 → 继续
        assert (
            sup_poor.should_continue(
                iteration=1,
                max_iterations=3,
                strategy={},
                backtest_result={"sharpe_ratio": 0.3},
                reflection="",
                improvement_suggestions="",
            )
            is True
        )


class TestSearchExperienceMinSharpeBoundary:
    """search_experience min_sharpe 过滤的边界正确性

    防止 `s = meta.get("sharpe_ratio", 0) or fallback` 把合法 sharpe=0
    当成"缺失"误回退；min_sharpe=None 时不过滤、min_sharpe=0.0 时过滤负 sharpe。
    """

    def _make_service(self):
        from long_earn.services.memory_service import MemoryServiceImpl

        config = MagicMock()
        service = MemoryServiceImpl(config, MagicMock())
        return service

    def test_zero_sharpe_strategy_excluded_when_min_sharpe_is_zero(self):
        """sharpe=0 的策略在 min_sharpe=0 时不应被过滤（0 >= 0 通过），
        但 sharpe=-0.1 必须过滤。"""
        svc = self._make_service()

        def fake_store_search(query, k=10, **kw):
            return [
                {
                    "content": "A",
                    "metadata": {
                        "experience_type": "strategy",
                        "term": "Bad",
                        "backtest_metrics": {"sharpe_ratio": -0.1},
                    },
                    "similarity": 0.9,
                },
                {
                    "content": "B",
                    "metadata": {
                        "experience_type": "strategy",
                        "term": "Zero",
                        "backtest_metrics": {"sharpe_ratio": 0.0},
                    },
                    "similarity": 0.8,
                },
                {
                    "content": "C",
                    "metadata": {
                        "experience_type": "strategy",
                        "term": "Good",
                        "backtest_metrics": {"sharpe_ratio": 1.5},
                    },
                    "similarity": 0.7,
                },
            ]

        svc._store.search = fake_store_search  # type: ignore[method-assign]

        result = svc.search_experience(query="x", k=5, min_sharpe=0.0)

        names = {r.name for r in result}
        # Bad (sharpe=-0.1) 必须被过滤
        assert "Bad" not in names, "min_sharpe=0 应过滤负 sharpe"
        # Zero (sharpe=0.0) 通过 (0 >= 0)
        assert "Zero" in names, "0 or fallback 旧 bug：合法 sharpe=0 被误判为缺失"
        # Good 通过
        assert "Good" in names

    def test_missing_sharpe_filtered_when_min_sharpe_set(self):
        """metadata 完全没有 sharpe_ratio 字段时，min_sharpe 被设值就排除（保守）"""
        svc = self._make_service()

        def fake_store_search(query, k=10, **kw):
            return [
                {
                    "content": ".",
                    "metadata": {
                        "experience_type": "strategy",
                        "term": "NoMetric",
                        "backtest_metrics": {},  # 没 sharpe_ratio
                    },
                    "similarity": 0.9,
                },
            ]

        svc._store.search = fake_store_search  # type: ignore[method-assign]

        result = svc.search_experience(query="x", k=5, min_sharpe=0.5)
        assert result == [], "min_sharpe 设值且元数据无 sharpe → 必须排除"

    def test_min_sharpe_none_no_filter(self):
        """min_sharpe=None 时不应过滤，含负 sharpe 也通过"""
        svc = self._make_service()

        def fake_store_search(query, k=10, **kw):
            return [
                {
                    "content": ".",
                    "metadata": {
                        "experience_type": "strategy",
                        "term": "Anything",
                        "backtest_metrics": {"sharpe_ratio": -2.0},
                    },
                    "similarity": 0.9,
                },
            ]

        svc._store.search = fake_store_search  # type: ignore[method-assign]

        result = svc.search_experience(query="x", k=5, min_sharpe=None)
        assert len(result) == 1
        assert result[0].name == "Anything"


class TestMemorySaveExperience:
    """记忆系统保存经验时必须把回测指标存进 backtest_metrics 元数据"""

    def test_flat_backtest_keys_are_persisted(self):
        from long_earn.services.memory_service import MemoryServiceImpl

        config = MagicMock()
        service = MemoryServiceImpl(config, MagicMock(spec=LoggerService))

        exp_id = service.save_experience(
            StrategyExperience(
                name="X",
                code="yaml",
                rationale="r",
                metrics={
                    "total_return": 0.42,
                    "sharpe_ratio": 1.2,
                    "max_drawdown": -0.1,
                },
                reflection="ok",
            )
        )

        assert exp_id  # 返回非空 ID
        substances = service._store.get_all()
        assert len(substances) == 1
        meta = substances[0].metadata
        metrics = meta.get("backtest_metrics", {})
        # 扁平字段被持久化
        assert metrics.get("total_return") == 0.42
        assert metrics.get("sharpe_ratio") == 1.2
        assert metrics.get("max_drawdown") == -0.1
        assert meta.get("backtest_success") is True
