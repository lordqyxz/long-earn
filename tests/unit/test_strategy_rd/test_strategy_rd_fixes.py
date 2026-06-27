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
    mock_config.memory_path = "~/.long_earn/memory.npz"
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


class TestGraphStructure:
    def test_subgraph_compiles(self):
        from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph

        context = _make_mock_context()
        subgraph = create_strategy_rd_subgraph(context)
        assert subgraph is not None


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

    def test_optimize_node_prefers_optimized_over_initial(self):
        """_optimize_node 在 state 同时含 strategy 和 optimized_strategy 时，
        必须使用后者作为优化起点 —— 这是多轮演进真正累积进化的关键。"""
        from long_earn.strategy_rd.subgraph import _optimize_node

        captured = {}

        class _StubAgent:
            def optimize_strategy(self, strategy, suggestions, previous_backtest=None):
                captured["base"] = strategy
                captured["bt"] = previous_backtest
                return {"strategy_name": "v3", "from_v2": True}

        state = {
            "strategy": {"strategy_name": "v0"},
            "optimized_strategy": {"strategy_name": "v2"},
            "improvement_suggestions": ["x"],
            "backtest_result": {"total_return": 0.1},
        }
        result = _optimize_node(state, _StubAgent(), logger=None)  # type: ignore[arg-type]

        # 关键：起点是 v2 而不是 v0
        assert captured["base"]["strategy_name"] == "v2"
        assert captured["bt"]["total_return"] == 0.1
        assert result["optimized_strategy"]["strategy_name"] == "v3"


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
            {"direction": "收益稳定性", "reflection": "", "improvement_suggestions": []},
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
            "sharpe_ratio": 0.1,   # 远低 _POOR_SHARPE_THRESHOLD 0.3
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
            "sharpe_ratio": 0.8,    # 中等
            "max_drawdown": -0.10,   # 轻微
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
        assert any("止损" in s or "回撤" in s for s in result["improvement_suggestions"])
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

    def _make_supervisor(self, llm_content: str | None = None, raises: Exception | None = None):
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


class TestRefineRoutingMultiRound:
    """多轮演进的 refine 路径正确性

    防止"第 2 轮回测优化版失败时，refine 误改初版且 control flow 跳回初版回测"
    这条灾难性 bug 复发。
    """

    def test_develop_optimized_resets_error_history(self):
        """develop_optimized 必须清空错误历史，否则第 1 轮累计的失败次数
        会让第 2 轮 refine 立即被 _refine_cond 判定 '已用尽预算'。"""
        from long_earn.strategy_rd.subgraph import _develop_optimized_node

        cleared = {"flag": False}

        class _StubAgent:
            def develop_strategy(self, _strategy):
                return "name: opt\nsignals: []"

            def clear_error_history(self):
                cleared["flag"] = True

            def get_error_history(self):
                return []

        state = {"optimized_strategy": {"strategy_name": "v2"}}
        out = _develop_optimized_node(state, _StubAgent(), logger=None)  # type: ignore[arg-type]
        assert cleared["flag"] is True
        assert "optimized_strategy_yaml" in out
        assert out["code_valid"] is False

    def test_refine_node_target_optimized_writes_optimized_yaml(self):
        """target='optimized' 时 refine_node 必须读 optimized_strategy_yaml，
        修复后写回 optimized_strategy_yaml（不是 strategy_yaml）。"""
        from long_earn.strategy_rd.subgraph import _refine_node

        captured = {}

        class _StubAgent:
            def refine_code(self, strategy, error_message, failed_code):
                captured["failed_code"] = failed_code
                captured["strategy"] = strategy
                return "FIXED_OPTIMIZED_YAML"

            def get_error_history(self):
                return []

        state = {
            "strategy": {"strategy_name": "v0"},
            "optimized_strategy": {"strategy_name": "v2"},
            "strategy_yaml": "INITIAL_YAML",
            "optimized_strategy_yaml": "BROKEN_OPTIMIZED_YAML",
            "backtest_result": {"error": "syntax X", "error_category": "code_logic"},
        }
        out = _refine_node(
            state, _StubAgent(), logger=None, target="optimized"  # type: ignore[arg-type]
        )

        # 关键：修的是优化版的代码，不是初版
        assert captured["failed_code"] == "BROKEN_OPTIMIZED_YAML"
        # 修复结果写回 optimized_strategy_yaml，不是 strategy_yaml
        assert out.get("optimized_strategy_yaml") == "FIXED_OPTIMIZED_YAML"
        assert "strategy_yaml" not in out
        # strategy 字段也以优化版为准
        assert captured["strategy"]["strategy_name"] == "v2"

    def test_refine_node_target_initial_writes_initial_yaml(self):
        """默认 target='initial' 时 refine_node 改 strategy_yaml（兼容旧路径）"""
        from long_earn.strategy_rd.subgraph import _refine_node

        class _StubAgent:
            def refine_code(self, strategy, error_message, failed_code):
                return "FIXED_INIT"

            def get_error_history(self):
                return []

        state = {
            "strategy": {"strategy_name": "v0"},
            "strategy_yaml": "BROKEN",
            "backtest_result": {"error": "x"},
        }
        out = _refine_node(state, _StubAgent(), logger=None)  # type: ignore[arg-type]

        assert out.get("strategy_yaml") == "FIXED_INIT"
        assert "optimized_strategy_yaml" not in out

    def test_refine_optimized_cond_routes_to_backtest_optimized(self):
        """refine_optimized 后路由必须回到 backtest_optimized，不能跑回 backtest（初版）"""
        from long_earn.strategy_rd.subgraph import _refine_optimized_cond

        class _BudgetLeftAgent:
            def get_error_history(self):
                return [{"e": 1}]  # 1 < MAX_CODE_REFINES

        class _BudgetUsedAgent:
            def get_error_history(self):
                return [{"e": 1}, {"e": 2}, {"e": 3}]

        assert _refine_optimized_cond({}, _BudgetLeftAgent()) == "backtest_optimized"  # type: ignore[arg-type]
        assert _refine_optimized_cond({}, _BudgetUsedAgent()) == "reflection"  # type: ignore[arg-type]

    def test_backtest_optimized_cond_routes_to_refine_optimized(self):
        """backtest_optimized 失败时必须路由到 refine_optimized 而不是共享的 refine"""
        from long_earn.strategy_rd.subgraph import _backtest_optimized_cond

        # code_valid=False → 修复路径
        assert _backtest_optimized_cond({"code_valid": False}) == "refine_optimized"
        # code_valid=True → reflection
        assert _backtest_optimized_cond({"code_valid": True}) == "reflection"


class TestMultiRoundEvolutionStaticE2E:
    """策略研发子图多轮演进静态 e2e 串行测试

    用 stub agent 跑过 init → research → develop → backtest → reflection →
    save_experience → supervisor → optimize → develop_optimized → backtest_optimized
    完整链路两轮，验证：
    - 第 1 轮 backtest 结果以扁平字段进入 state；
    - reflection 能用 `_identify_primary_issue` 从扁平字段拿到 sharpe；
    - save_experience 第 1 轮被调用；
    - optimize 起点是上一轮 optimized_strategy（如果存在）而非初始 strategy；
    - 第 2 轮 backtest 结果重新覆写 state；
    - evolution_lineage 在第 2 轮记录到深度 2。
    """

    def test_two_round_evolution_state_flow(self):
        from long_earn.strategy_rd.subgraph import (
            _backtest_node,
            _backtest_optimized_node,
            _develop_node,
            _develop_optimized_node,
            _optimize_node,
            _reflection_node,
            _save_experience_node,
            _supervisor_node,
        )

        # ── stub agents ───────────────────────────
        class _StubResearchAgent:
            def __init__(self):
                self.calls: list = []

            def reflect(self, strategy, backtest_result):
                self.calls.append(("reflect", strategy.get("strategy_name")))
                return {
                    "reflection": "需要降低回撤",
                    "improvement_suggestions": ["增加止损"],
                    "selected_direction": "风险控制",
                    "tot_enabled": False,
                    "primary_issue": "max_drawdown 过大",
                }

            def optimize_strategy(self, strategy, suggestions, previous_backtest=None):
                self.calls.append(
                    ("optimize", strategy.get("strategy_name"), bool(previous_backtest))
                )
                lineage = list(strategy.get("evolution_lineage", []) or [])
                lineage.append({"from": strategy.get("strategy_name", "?")})
                return {
                    "strategy_name": f"{strategy.get('strategy_name', 'v')}_opt",
                    "evolution_lineage": lineage,
                    "optimized": True,
                }

        class _StubDevelopAgent:
            def develop_strategy(self, strategy):
                return f"name: {strategy.get('strategy_name', 'X')}\nsignals: []"

            def get_error_history(self):
                return []

            def clear_error_history(self):
                pass

        class _StubBacktestService:
            def __init__(self):
                self.round = 0

            def run(self, strategy_yaml, start_date="", end_date=""):
                self.round += 1
                return {
                    "total_return": 0.1 * self.round,
                    "annual_return": 0.12 * self.round,
                    "sharpe_ratio": 0.5 + 0.3 * self.round,
                    "max_drawdown": -0.15,
                    "win_rate": 0.55,
                    "trading_days": 250,
                    "volatility": 0.18,
                    "calmar_ratio": 0.8,
                    "sortino_ratio": 0.7,
                    "daily_returns": [],
                    "strategy_diagnostics": {
                        "factor_failures": [],
                        "step_failures": [],
                        "trade_count": 30,
                        "degenerate": False,
                    },
                }

        class _StubSupervisor:
            def __init__(self):
                self.calls = 0

            def should_continue(self, **_kw):
                self.calls += 1
                return self.calls == 1  # 第 1 轮继续，第 2 轮停止

        class _StubMemory:
            def __init__(self):
                self.saved = []

            def save_experience(self, experience):
                self.saved.append(experience)
                return "exp-id"

            def search_experience(self, **_kw):
                return []

        research = _StubResearchAgent()
        develop = _StubDevelopAgent()
        backtest = _StubBacktestService()
        supervisor = _StubSupervisor()
        memory = _StubMemory()

        # ── 第 1 轮 ───────────────────────────────
        state: dict = {
            "strategy": {"strategy_name": "v1"},
            "iteration": 0,
            "max_iterations": 2,
        }

        # develop → backtest
        state.update(_develop_node(state, develop, logger=None))  # type: ignore[arg-type]
        bt = _backtest_node(state, backtest, logger=None)  # type: ignore[arg-type]
        state.update(bt)
        # 关键断言 1：扁平字段进入 state，且策略诊断未退化
        assert state["backtest_result"]["sharpe_ratio"] == 0.8
        assert state["backtest_result"]["strategy_diagnostics"]["degenerate"] is False
        assert state["code_valid"] is True

        # reflection
        state.update(_reflection_node(state, research, logger=None))  # type: ignore[arg-type]
        assert state["reflection"] == "需要降低回撤"
        assert state["primary_issue"] == "max_drawdown 过大"

        # save_experience
        state.update(
            _save_experience_node(state, memory, develop, logger=None)  # type: ignore[arg-type]
        )
        assert state["experience_saved"] is True
        assert len(memory.saved) == 1
        # 关键断言 2：扁平回测字段在 metrics 中可见，可被记忆系统读到
        assert memory.saved[0].metrics["sharpe_ratio"] == 0.8

        # supervisor → 决定继续
        state.update(
            _supervisor_node(state, supervisor, logger=None)  # type: ignore[arg-type]
        )
        assert state["should_continue"] is True
        assert state["iteration"] == 1

        # optimize → develop_optimized → backtest_optimized
        state.update(_optimize_node(state, research, logger=None))  # type: ignore[arg-type]
        # 关键断言 3：optimize 起点是 v1（无上一轮 optimized）
        assert ("optimize", "v1", True) in research.calls
        assert state["optimized_strategy"]["strategy_name"] == "v1_opt"

        state.update(
            _develop_optimized_node(state, develop, logger=None)  # type: ignore[arg-type]
        )
        bt2 = _backtest_optimized_node(state, backtest, logger=None)  # type: ignore[arg-type]
        state.update(bt2)
        # 关键断言 4：第 2 轮回测覆写 state
        assert state["backtest_result"]["sharpe_ratio"] == 1.1  # 0.5 + 0.3*2
        assert state["code_valid"] is True

        # ── 第 2 轮 reflection / save / supervisor ─
        state.update(_reflection_node(state, research, logger=None))  # type: ignore[arg-type]
        state.update(
            _save_experience_node(state, memory, develop, logger=None)  # type: ignore[arg-type]
        )
        # save_experience 被调用 2 次（每轮一次）
        assert len(memory.saved) == 2
        # 第 2 次保存的指标应是第 2 轮的真实业绩
        assert memory.saved[1].metrics["sharpe_ratio"] == 1.1

        state.update(
            _supervisor_node(state, supervisor, logger=None)  # type: ignore[arg-type]
        )
        # 第 2 次调用应返回 False（停止）
        assert state["should_continue"] is False
        assert state["iteration"] == 2

        # ── 关键断言 5：如果还有第 3 轮，optimize 起点会是 v1_opt（演进累积） ─
        # 模拟"假如 supervisor 再次返回 True"的 optimize 调用
        research.calls.clear()
        _optimize_node(state, research, logger=None)  # type: ignore[arg-type]
        assert ("optimize", "v1_opt", True) in research.calls, (
            f"期望第 3 轮起点为 v1_opt（上一轮成果累积），实际 calls={research.calls}"
        )


class TestSearchExperienceMinSharpeBoundary:
    """search_experience min_sharpe 过滤的边界正确性

    防止 `s = meta.get("sharpe_ratio", 0) or fallback` 把合法 sharpe=0
    当成"缺失"误回退；min_sharpe=None 时不过滤、min_sharpe=0.0 时过滤负 sharpe。
    """

    def _make_service(self):
        from long_earn.services.memory_service import MemoryServiceImpl

        config = MagicMock()
        config.memory_path = ":memory:"
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
        config.memory_path = ":memory:"
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
