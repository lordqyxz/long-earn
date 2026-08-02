"""ToG 策略研发智能体（ADR-018）

LLM ⊗ Graph：在 Substance / Ontology 上 explore + prune，
用回测与统计门作不可跳过的证据工具，写回路径结果形成飞轮。

假设树与 ADR-015 门保留为状态/硬约束；本模块替代 HTR 固定六步作为探索控制器。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from long_earn.core.prompt_loader import MarkdownPromptTemplate

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext
    from long_earn.services import LoggerService, MonitoringService

_DEFAULT_RECURSION_LIMIT = 50
_BEAM_WIDTH = 3


class ResearchAgent:
    """Think-on-Graph 风格策略研发智能体。

    用法::

        context = initialize_context()
        agent = ResearchAgent(context)
        result = agent.invoke("研发动量策略并补齐缺失算子")
    """

    def __init__(
        self,
        context: RuntimeContext,
        *,
        checkpointer: Any = None,
    ) -> None:
        self.context = context
        self._logger: LoggerService = context.logger
        self._monitoring: MonitoringService = context.monitoring
        self._checkpointer = checkpointer
        # beam 路径状态（进程内，单次 invoke 生命周期）
        self._beam_paths: list[dict[str, Any]] = []
        self._last_context: str = ""

        prompt_template = MarkdownPromptTemplate(
            "research_agent_prompt.md",
            caller_file=__file__,
        )
        system_prompt = prompt_template.format()
        llm = context.require_llm().get_llm()
        agent_kwargs: dict[str, Any] = {
            "model": llm,
            "tools": self._build_tools(),
            "prompt": system_prompt,
        }
        if checkpointer is not None:
            agent_kwargs["checkpointer"] = checkpointer
        self._agent = create_react_agent(**agent_kwargs)

    def _build_tools(self) -> list[Any]:
        return [
            self._make_prepare_context_tool(),
            self._make_activate_subgraph_tool(),
            self._make_expand_relations_tool(),
            self._make_prune_paths_tool(),
            self._make_list_operators_tool(),
            self._make_develop_operator_tool(),
            self._make_compile_strategy_yaml_tool(),
            self._make_run_backtest_tool(),
            self._make_run_oos_gates_tool(),
            self._make_prove_causality_tool(),
            self._make_record_path_outcome_tool(),
        ]

    # ── 图工具 ─────────────────────────────────────────────────

    def _make_prepare_context_tool(self) -> Any:
        logger = self._logger
        monitoring = self._monitoring
        ctx = self.context
        agent = self

        @tool
        def prepare_context(query: str, refresh_events: bool = False) -> str:
            """锚定研究问题：激活事件/知识子图；必要时自动采集事件。

            Args:
                query: 研究意图或标的描述
                refresh_events: True 时强制再跑事件采集（即使已有激活结果）

            Returns:
                可注入后续推理的上下文摘要
            """
            with monitoring.track("research.prepare_context"):
                logger.info(f"[ToG] prepare_context: {query[:80]}")
                text = ctx.prepare_context(query, force_refresh=refresh_events)
                agent._last_context = text
                return (
                    text or "（无激活上下文；可继续 expand_relations / list_operators）"
                )

        return prepare_context

    def _make_activate_subgraph_tool(self) -> Any:
        logger = self._logger
        monitoring = self._monitoring
        memory = self.context.memory
        agent = self

        @tool
        def activate_subgraph(query: str, k: int = 5) -> str:
            """仅激活已有 Substance 事件/关系（不触发采集）。

            Args:
                query: 触发文本
                k: 返回条数上限
            """
            with monitoring.track("research.activate_subgraph"):
                logger.info(f"[ToG] activate_subgraph: {query[:80]}")
                if not hasattr(memory, "activate_events"):
                    return "记忆服务不支持 activate_events"
                events = memory.activate_events(query, k=k)
                text = "\n".join(events) if events else "（无命中）"
                agent._last_context = text
                return text

        return activate_subgraph

    def _make_expand_relations_tool(self) -> Any:
        logger = self._logger
        monitoring = self._monitoring
        ctx = self.context
        agent = self

        @tool
        def expand_relations(entity: str, max_depth: int = 2) -> str:
            """在 Ontology / Substance 图上扩展邻居（ToG on-graph 步）。

            Args:
                entity: 实体名、sid 或概念关键词
                max_depth: 图遍历深度（默认 2）
            """
            with monitoring.track("research.expand_relations"):
                logger.info(f"[ToG] expand_relations: {entity}")
                lines: list[str] = []
                connector = ctx.connector
                if connector is not None:
                    try:
                        paths = connector.graph.traverse(
                            entity,
                            max_depth=max_depth,
                            min_weight=0.0,
                        )
                        for p in paths[:20]:
                            node_name = p.node.name if p.node is not None else p.sid
                            lines.append(
                                f"- {p.sid} → {node_name} "
                                f"(w={getattr(p, 'weight', 0):.2f})"
                            )
                    except Exception as exc:
                        lines.append(f"ontology traverse: {exc}")

                if hasattr(ctx.memory, "activate_events"):
                    try:
                        extra = ctx.memory.activate_events(entity, k=5)
                        for e in extra:
                            lines.append(f"- event: {e[:200]}")
                    except Exception as exc:
                        lines.append(f"activate_events: {exc}")

                path_id = f"path_{len(agent._beam_paths)}"
                agent._beam_paths.append(
                    {
                        "id": path_id,
                        "entity": entity,
                        "neighbors": lines[: _BEAM_WIDTH * 4],
                        "status": "open",
                    }
                )
                if not lines:
                    return f"实体 {entity!r} 无邻居；已登记 beam {path_id}"
                return f"beam={path_id}\n" + "\n".join(lines[:20])

        return expand_relations

    def _make_prune_paths_tool(self) -> Any:
        logger = self._logger
        monitoring = self._monitoring
        agent = self

        @tool
        def prune_paths(keep_ids: str = "", drop_ids: str = "") -> str:
            """剪枝 beam 路径（ToG think 步）。

            Args:
                keep_ids: 逗号分隔的保留 path id
                drop_ids: 逗号分隔的丢弃 path id
            """
            with monitoring.track("research.prune_paths"):
                keep = {x.strip() for x in keep_ids.split(",") if x.strip()}
                drop = {x.strip() for x in drop_ids.split(",") if x.strip()}
                kept = 0
                for path in agent._beam_paths:
                    pid = path["id"]
                    if pid in drop or (keep and pid not in keep):
                        path["status"] = "pruned"
                    elif path["status"] != "pruned":
                        path["status"] = "active"
                        kept += 1
                logger.info(f"[ToG] prune_paths: active={kept}")
                active = [p for p in agent._beam_paths if p["status"] == "active"]
                return json.dumps(
                    {"active": active[:_BEAM_WIDTH], "total_active": kept},
                    ensure_ascii=False,
                )

        return prune_paths

    # ── 研发工具 ───────────────────────────────────────────────

    def _make_list_operators_tool(self) -> Any:
        monitoring = self._monitoring

        @tool
        def list_operators_tool(category: str = "") -> str:
            """列出算子目录中已注册算子。

            Args:
                category: 可选类别过滤（technical / factor / filter / compose）
            """
            with monitoring.track("research.list_operators"):
                from long_earn.backtest.operators import (  # noqa: PLC0415
                    list_operators,
                )

                catalog = list_operators()
                if category:
                    catalog = {
                        k: v
                        for k, v in catalog.items()
                        if v.get("category") == category
                    }
                # 精简输出
                slim = {
                    name: {
                        "category": meta.get("category"),
                        "description": (meta.get("description") or "")[:120],
                    }
                    for name, meta in catalog.items()
                }
                return json.dumps(slim, ensure_ascii=False, indent=2)

        return list_operators_tool

    def _make_develop_operator_tool(self) -> Any:
        logger = self._logger
        monitoring = self._monitoring
        ctx = self.context
        agent = self

        @tool
        def develop_operator(name: str, intent: str, category: str = "factor") -> str:
            """研发缺失算子：写入 OperatorBacklog 并尝试跑 operator_dev 子图一轮。

            Args:
                name: 算子名（snake_case）
                intent: 研发意图描述
                category: 算子类别
            """
            with monitoring.track("research.develop_operator"):
                logger.info(f"[ToG] develop_operator: {name}")
                backlog = ctx.operator_backlog
                if backlog is None:
                    return "operator_backlog 未注入，无法研发算子"
                try:
                    from long_earn.operator_dev.spec import (  # noqa: PLC0415
                        OperatorSpec,
                        OperatorSpecPriority,
                    )

                    # 最小合法 YAML 占位，满足 reference_strategy 非空契约
                    stub_yaml = (
                        f"name: stub_for_{name}\n"
                        "universe:\n  type: main_board+gem\n"
                        "operator_factors: []\n"
                        "signals: []\n"
                    )
                    spec = OperatorSpec(
                        name=name,
                        intent=intent,
                        input_fields=["close"],
                        category=category,
                        expected_output="float series",
                        reference_strategy=stub_yaml,
                        motivation=intent,
                        priority=OperatorSpecPriority.HIGH,
                    )
                    accepted = backlog.submit(spec)
                    if not accepted:
                        return f"算子 {name} 已在 backlog 中"
                except Exception as exc:
                    return f"写入 backlog 失败: {exc}"

                try:
                    from long_earn.operator_dev.subgraph import (  # noqa: PLC0415
                        create_operator_dev_subgraph,
                    )

                    subgraph = create_operator_dev_subgraph(
                        ctx,
                        backlog=backlog,
                        checkpointer=agent._checkpointer,
                    )
                    invoke_cfg: dict[str, Any] = {}
                    if agent._checkpointer is not None:
                        invoke_cfg = {
                            "configurable": {
                                "thread_id": f"opdev-{name}",
                            }
                        }
                    result = subgraph.invoke({}, config=invoke_cfg or None)
                    return json.dumps(
                        {
                            "enqueued": name,
                            "result_keys": list(result.keys())
                            if isinstance(result, dict)
                            else [],
                            "summary": str(result)[:800],
                        },
                        ensure_ascii=False,
                    )
                except Exception as exc:
                    return f"算子已入队 {name}，子图执行失败: {exc}"

        return develop_operator

    def _make_compile_strategy_yaml_tool(self) -> Any:
        monitoring = self._monitoring

        @tool
        def compile_strategy_yaml(strategy_yaml: str) -> str:
            """校验并解析策略 YAML（算子目录 DSL）。

            Args:
                strategy_yaml: 完整策略 YAML 文本
            """
            with monitoring.track("research.compile_strategy_yaml"):
                from long_earn.backtest.engine.dsl import (  # noqa: PLC0415
                    parse_strategy_yaml,
                )

                try:
                    dsl = parse_strategy_yaml(strategy_yaml)
                    return json.dumps(
                        {
                            "ok": True,
                            "name": dsl.name,
                            "operator_factors": len(dsl.operator_factors),
                            "signals": len(dsl.signals),
                        },
                        ensure_ascii=False,
                    )
                except Exception as exc:
                    return json.dumps(
                        {"ok": False, "error": str(exc)},
                        ensure_ascii=False,
                    )

        return compile_strategy_yaml

    # ── 证据工具 ───────────────────────────────────────────────

    def _make_run_backtest_tool(self) -> Any:
        logger = self._logger
        monitoring = self._monitoring
        ctx = self.context

        @tool
        def run_backtest(strategy_yaml: str, use_train_split: bool = True) -> str:
            """在训练集（或显式区间）上跑回测——证据工具，不可用直觉替代。

            Args:
                strategy_yaml: 策略 YAML
                use_train_split: True 时强制使用 config 训练集日期
            """
            with monitoring.track("research.run_backtest"):
                start = ""
                end = ""
                if use_train_split:
                    start = ctx.config.train_start_date
                    end = ctx.config.train_end_date
                logger.info(
                    f"[ToG] run_backtest: {start or '(DSL区间)'}~{end or '(DSL区间)'}"
                )
                result = ctx.backtest_service.run(
                    strategy_yaml=strategy_yaml,
                    start_date=start,
                    end_date=end,
                )
                if not isinstance(result, dict):
                    return json.dumps(
                        {"error": "回测返回格式异常", "metrics_unreliable": True},
                        ensure_ascii=False,
                    )
                diag = result.get("strategy_diagnostics") or {}
                unreliable = bool(result.get("metrics_unreliable"))
                slim: dict[str, Any] = {
                    "error": result.get("error"),
                    "total_return": result.get("total_return"),
                    "sharpe_ratio": result.get("sharpe_ratio"),
                    "max_drawdown": result.get("max_drawdown"),
                    "trade_count": diag.get("trade_count", result.get("trade_count")),
                    "metrics_unreliable": unreliable,
                    "degenerate": diag.get("degenerate"),
                    "failed_factor_aliases": diag.get("failed_factor_aliases", []),
                    "failed_step_labels": diag.get("failed_step_labels", []),
                    "engine_metrics_unreliable": diag.get(
                        "engine_metrics_unreliable", False
                    ),
                }
                if unreliable:
                    slim["rejected"] = True
                    slim["rejection_reason"] = (
                        "训练集回测指标不可信，不得作为策略有效证据"
                    )
                return json.dumps(slim, ensure_ascii=False, default=str)

        return run_backtest

    def _make_run_oos_gates_tool(self) -> Any:
        logger = self._logger
        monitoring = self._monitoring
        ctx = self.context

        @tool
        def run_oos_gates(strategy_yaml: str) -> str:
            """Walk-Forward OOS + 统计门（稳定性）——合并前必须通过。

            Args:
                strategy_yaml: 策略 YAML
            """
            with monitoring.track("research.run_oos_gates"):
                logger.info("[ToG] run_oos_gates")
                bt = ctx.backtest_service

                from long_earn.strategy_optimization.acceptance import (  # noqa: PLC0415
                    is_metrics_unreliable,
                )

                train_bt = bt.run(
                    strategy_yaml=strategy_yaml,
                    start_date=ctx.config.train_start_date,
                    end_date=ctx.config.train_end_date,
                )
                if isinstance(train_bt, dict) and is_metrics_unreliable(train_bt):
                    diag = train_bt.get("strategy_diagnostics") or {}
                    return json.dumps(
                        {
                            "passed": False,
                            "reason": (
                                "训练集回测指标不可信"
                                f"（degenerate={diag.get('degenerate')}, "
                                f"factor_failures={diag.get('failed_factor_aliases')}, "
                                f"step_failures={diag.get('failed_step_labels')}）"
                                "，禁止进入 OOS 门"
                            ),
                            "metrics_unreliable": True,
                        },
                        ensure_ascii=False,
                    )

                try:
                    oos = bt.run_walk_forward_parallel(
                        strategy_yaml=strategy_yaml,
                        start_date=ctx.config.test_start_date,
                        end_date=ctx.config.test_end_date,
                    )
                except Exception as exc:
                    return json.dumps(
                        {"passed": False, "error": f"OOS 执行失败: {exc}"},
                        ensure_ascii=False,
                    )

                fold_results = []
                if isinstance(oos, dict):
                    fold_results = oos.get("fold_results") or oos.get("folds") or []
                    if not fold_results and "metrics" in oos:
                        fold_results = [oos]

                from long_earn.strategy_optimization.overfit_gates import (  # noqa: PLC0415
                    WalkForwardStabilityGate,
                )

                stability = WalkForwardStabilityGate().evaluate(fold_results)
                return json.dumps(
                    {
                        "passed": stability.passed,
                        "reason": stability.reason,
                        "worst_fold_sharpe": stability.worst_fold_sharpe,
                        "fold_sharpe_std": stability.fold_sharpe_std,
                        "consistency_ratio": stability.consistency_ratio,
                    },
                    ensure_ascii=False,
                )

        return run_oos_gates

    def _make_prove_causality_tool(self) -> Any:
        monitoring = self._monitoring

        @tool
        def prove_causality_tool(operator_name: str) -> str:
            """对已注册算子跑 prove_causality 数值证明。

            Args:
                operator_name: 算子名
            """
            with monitoring.track("research.prove_causality"):
                import polars as pl  # noqa: PLC0415

                from long_earn.backtest.operators import get_operator  # noqa: PLC0415
                from long_earn.backtest.operators.causality import (  # noqa: PLC0415
                    prove_causality,
                )

                try:
                    op = get_operator(operator_name)
                except Exception as exc:
                    return json.dumps(
                        {"passed": False, "error": str(exc)},
                        ensure_ascii=False,
                    )

                # 最小因果性面板
                panel = pl.DataFrame(
                    {
                        "date": [
                            "2024-01-01",
                            "2024-01-02",
                            "2024-01-03",
                            "2024-01-04",
                        ],
                        "symbol": ["AAA"] * 4,
                        "close": [10.0, 11.0, 10.5, 12.0],
                        "volume": [1000, 1100, 900, 1200],
                    }
                )
                try:
                    params = op.params_cls()
                except Exception:
                    params = op.params_cls  # type: ignore[assignment]
                    try:
                        params = op.params_cls()  # retry
                    except Exception as exc:
                        return json.dumps(
                            {"passed": False, "error": f"params: {exc}"},
                            ensure_ascii=False,
                        )

                try:
                    reports = prove_causality(op, params, panel)
                    passed = all(r.passed for r in reports)
                    return json.dumps(
                        {
                            "passed": passed,
                            "reports": [
                                {"passed": r.passed, "detail": str(r)[:200]}
                                for r in reports
                            ],
                        },
                        ensure_ascii=False,
                    )
                except Exception as exc:
                    return json.dumps(
                        {"passed": False, "error": str(exc)},
                        ensure_ascii=False,
                    )

        return prove_causality_tool

    def _make_record_path_outcome_tool(self) -> Any:
        logger = self._logger
        monitoring = self._monitoring
        ctx = self.context

        @tool
        def record_path_outcome(
            path_summary: str,
            strategy_yaml: str = "",
            metrics_json: str = "",
            reflection: str = "",
        ) -> str:
            """将探索路径结果写回 Substance（飞轮）。

            Args:
                path_summary: 路径摘要 / 策略名
                strategy_yaml: 策略 YAML（可空）
                metrics_json: 指标 JSON 字符串
                reflection: 反思文本
            """
            with monitoring.track("research.record_path_outcome"):
                logger.info(f"[ToG] record_path_outcome: {path_summary[:80]}")
                metrics: dict[str, Any] = {}
                if metrics_json.strip():
                    try:
                        metrics = json.loads(metrics_json)
                    except json.JSONDecodeError:
                        metrics = {"raw": metrics_json[:500]}
                from long_earn.services import StrategyExperience  # noqa: PLC0415

                exp = StrategyExperience(
                    name=path_summary[:80],
                    code=strategy_yaml[:4000],
                    rationale=path_summary,
                    metrics=metrics,
                    reflection=reflection,
                )
                try:
                    sid = ctx.memory.save_experience(exp)
                    return f"已写回经验 sid={sid}"
                except Exception as exc:
                    return f"写回失败: {exc}"

        return record_path_outcome

    def invoke(
        self,
        idea: str,
        constraints: str = "",
        *,
        thread_id: str = "",
    ) -> dict[str, Any]:
        """执行一次 ToG 策略研发。

        Args:
            idea: 研发想法
            constraints: 可选约束
            thread_id: 启用 checkpointer 时的线程 ID；空则用默认 ``tog-research``

        Returns:
            summary / messages / beam_paths
        """
        self._beam_paths = []
        query = idea if not constraints else f"{idea} (约束: {constraints})"
        # 入口自动准备上下文（基础设施，非可选）
        try:
            self._last_context = self.context.prepare_context(query)
        except Exception as exc:
            self._logger.warning(f"prepare_context 失败（非致命）: {exc}")
            self._last_context = ""

        user_content = query
        if self._last_context:
            user_content = f"{query}\n\n## 已激活上下文\n\n{self._last_context[:3000]}"

        run_config: dict[str, Any] = {"recursion_limit": _DEFAULT_RECURSION_LIMIT}
        if self._checkpointer is not None:
            tid = thread_id.strip() or "tog-research"
            run_config["configurable"] = {"thread_id": tid}
            self._logger.info(f"[ToG] checkpoint 已启用，thread_id={tid}")

        with self._monitoring.track("research_agent"):
            result = self._agent.invoke(
                {"messages": [("user", user_content)]},
                config=run_config,
            )

        messages = result.get("messages", [])
        summary = ""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                summary = str(msg.content)
                break

        return {
            "summary": summary or "策略研发完成（无最终文本）",
            "result": summary or "策略研发完成（无最终文本）",
            "messages": messages,
            "beam_paths": list(self._beam_paths),
            "event_context": self._last_context,
        }
