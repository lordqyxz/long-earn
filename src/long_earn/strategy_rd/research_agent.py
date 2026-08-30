"""ToG 策略研发智能体（ADR-018 / ADR-022）

LLM ⊗ Graph：在 Substance / Ontology 上 explore + prune，
用回测与统计验证门控作不可跳过的证据工具，写回路径结果形成飞轮。

硬性门控：Walk-Forward 稳定性 + held-out 相对 current best。
诊断门控：DSR / PBO（缺料 skipped）。HTR 编排已 Deprecated。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from long_earn.core.prompt_loader import MarkdownPromptTemplate
from long_earn.event_inference import create_event_inference_subgraph

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext
    from long_earn.services import LoggerService, MonitoringService

_DEFAULT_RECURSION_LIMIT = 50
_BEAM_WIDTH = 3

# success 写回时由证据覆盖、禁止 LLM metrics 篡改的键
_PROTECTED_WRITEBACK_KEYS = frozenset(
    {
        "sharpe_ratio",
        "total_return",
        "max_drawdown",
        "metrics_unreliable",
        "degenerate",
        "trade_count",
        "worst_fold_sharpe",
        "fold_sharpe_std",
        "consistency_ratio",
        "stability",
        "merge",
        "dsr",
        "pbo",
        "n_eff_trials",
        "passed",
        "reason",
        "dsr_status",
        "outcome",
        "error",
    }
)


def _daily_returns_as_floats(
    daily_returns: list[dict[str, Any]] | list[float] | None,
) -> list[float]:
    """从回测 daily_returns 提取 float 序列（与 overfit_gates 解析一致）。"""
    if not daily_returns:
        return []
    values: list[float] = []
    for item in daily_returns:
        if isinstance(item, (int, float)):
            values.append(float(item))
        elif isinstance(item, dict) and "value" in item:
            try:
                values.append(float(item["value"]))
            except (TypeError, ValueError):
                continue
    return values


def _pbo_method_from_reason(reason: str) -> str:
    """从 PBO reason 解析 method 标签。"""
    match = re.search(r"method=([\w_]+)", reason)
    if match:
        return match.group(1)
    return "unknown"


def _strategy_fingerprint(strategy_yaml: str) -> str:
    """策略 YAML 摘要指纹，用于证据与写回路径对齐。"""
    return hashlib.sha256(strategy_yaml.strip().encode()).hexdigest()[:16]


def _evidence_metrics_block_reason(metrics: dict[str, Any]) -> str | None:
    """指标是否不足以支撑 success 写回；无问题时返回 None。"""
    if metrics.get("metrics_unreliable"):
        return "指标不可信 (metrics_unreliable)"
    if metrics.get("degenerate"):
        return "策略退化 (degenerate)"
    trade_count = metrics.get("trade_count")
    if trade_count is not None and int(trade_count) == 0:
        return "无交易 (trade_count=0)"
    if metrics.get("error"):
        return f"回测错误: {metrics['error']}"
    return None


@dataclass
class _StrategyEvidence:
    """单次 invoke 内缓存的回测 / OOS 证据（进程内，不写盘）。"""

    strategy_hash: str
    backtest_metrics: dict[str, Any] | None = None
    backtest_reliable: bool = False
    oos_passed: bool | None = None
    oos_metrics: dict[str, Any] = field(default_factory=dict)
    # DSR 诊断状态：passed / failed / skipped（ADR-022；不再作写回硬闸）
    oos_dsr_status: str | None = None


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
        # 证据门：strategy_hash -> 回测/OOS 缓存（同一次 invoke 内有效）
        self._evidence_cache: dict[str, _StrategyEvidence] = {}
        # 试验登记（ADR-022）：调用次数 + 唯一指纹 → N_eff
        self._strategy_trial_count: int = 0
        self._trial_fingerprints: set[str] = set()
        # PBO 候选矩阵：session 内 (IS sharpe, OOS mean sharpe) 与日收益列
        self._oos_candidate_pairs: list[tuple[float, float]] = []
        self._oos_return_columns: list[list[float]] = []
        # held-out 合并基线（session 内 current best OOS mean sharpe）
        self._current_best_oos: float | None = None
        # 事件采集推理子图（ADR-021：miss/强制刷新时在 agent 层显式触发）
        self._event_inference = create_event_inference_subgraph(context)

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

    def _register_trial(self, strategy_yaml: str, *, count: int = 1) -> int:
        """登记试验；返回当前有效试验数 N_eff（唯一指纹数）。"""
        self._strategy_trial_count += max(count, 1)
        self._trial_fingerprints.add(_strategy_fingerprint(strategy_yaml))
        return max(len(self._trial_fingerprints), 1)

    @property
    def _n_eff_trials(self) -> int:
        return max(len(self._trial_fingerprints), 1)

    def _cache_backtest_evidence(
        self, strategy_yaml: str, metrics: dict[str, Any]
    ) -> None:
        """缓存训练集回测证据（仅可靠指标）。"""
        fp = _strategy_fingerprint(strategy_yaml)
        ev = self._evidence_cache.get(fp)
        if ev is None:
            ev = _StrategyEvidence(strategy_hash=fp)
            self._evidence_cache[fp] = ev
        ev.backtest_metrics = dict(metrics)
        ev.backtest_reliable = True

    def _cache_oos_evidence(
        self,
        strategy_yaml: str,
        *,
        passed: bool,
        metrics: dict[str, Any],
        dsr_status: str | None = None,
    ) -> None:
        """缓存 OOS 门结果（``passed`` 仅反映硬性门控）。"""
        fp = _strategy_fingerprint(strategy_yaml)
        ev = self._evidence_cache.get(fp)
        if ev is None:
            ev = _StrategyEvidence(strategy_hash=fp)
            self._evidence_cache[fp] = ev
        ev.oos_passed = passed
        ev.oos_metrics = dict(metrics)
        if dsr_status is not None:
            ev.oos_dsr_status = dsr_status

    def _validate_success_writeback(
        self,
        strategy_yaml: str,
        metrics: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """校验 success 写回是否满足证据门契约。

        证据门（ADR-022）：
        1. 证据存在：必须有 run_backtest 或 run_oos_gates 证据
        2. OOS 硬性门控：``evidence.oos_passed is True``（稳定性 + 合并阈值）
        3. 指标可信：回测指标不可信直接拒绝
        4. DSR/PBO 仅为诊断，不硬拒

        Returns:
            (allowed, error_message, merged_metrics)
        """
        yaml_text = strategy_yaml.strip()
        if not yaml_text:
            return False, "拒绝写回成功：缺少 strategy_yaml，无法核对证据", metrics

        fp = _strategy_fingerprint(yaml_text)
        evidence = self._evidence_cache.get(fp)
        if evidence is None:
            return (
                False,
                "拒绝写回成功：无 run_backtest / run_oos_gates 证据，请先调用证据工具",
                metrics,
            )

        has_reliable_backtest = (
            evidence.backtest_reliable
            and evidence.backtest_metrics is not None
            and _evidence_metrics_block_reason(evidence.backtest_metrics) is None
        )
        if evidence.oos_passed is not True:
            if has_reliable_backtest:
                return (
                    False,
                    "拒绝写回成功：须 OOS 硬性门控通过；仅有训练集证据时可使用 outcome=candidate 写回候选",
                    metrics,
                )
            return (
                False,
                "拒绝写回成功：须 OOS 硬性门控通过，请先调用 run_oos_gates",
                metrics,
            )

        merged: dict[str, Any] = {}
        if evidence.backtest_metrics:
            merged.update(evidence.backtest_metrics)
        if evidence.oos_metrics:
            merged.update(evidence.oos_metrics)
            merge_block = evidence.oos_metrics.get("merge")
            if isinstance(merge_block, dict):
                oos_mean = merge_block.get("oos_mean_sharpe")
                if isinstance(oos_mean, (int, float)):
                    merged["sharpe_ratio"] = float(oos_mean)
        for key, value in metrics.items():
            if key not in _PROTECTED_WRITEBACK_KEYS:
                merged[key] = value

        block = _evidence_metrics_block_reason(merged)
        if block is not None:
            return False, f"拒绝写回成功：{block}", merged

        # ADR-022：DSR/PBO 诊断结果仅写入 metrics，不硬拒写回
        if evidence.oos_dsr_status is not None:
            merged["dsr_status"] = evidence.oos_dsr_status

        merged["outcome"] = "success"
        return True, "", merged

    def _validate_candidate_writeback(
        self,
        strategy_yaml: str,
        metrics: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """校验 candidate 写回：允许仅有可靠训练集回测证据的候选路径。

        Returns:
            (allowed, error_message, merged_metrics)
        """
        yaml_text = strategy_yaml.strip()
        if not yaml_text:
            return False, "拒绝写回候选：缺少 strategy_yaml，无法核对证据", metrics

        fp = _strategy_fingerprint(yaml_text)
        evidence = self._evidence_cache.get(fp)
        if evidence is None:
            return (
                False,
                "拒绝写回候选：无 run_backtest 证据，请先调用 run_backtest",
                metrics,
            )

        has_reliable_backtest = (
            evidence.backtest_reliable
            and evidence.backtest_metrics is not None
            and _evidence_metrics_block_reason(evidence.backtest_metrics) is None
        )
        if not has_reliable_backtest:
            return (
                False,
                "拒绝写回候选：需可靠训练集回测证据",
                metrics,
            )

        merged = dict(metrics)
        if not merged and evidence.backtest_metrics:
            merged = dict(evidence.backtest_metrics)

        block = _evidence_metrics_block_reason(merged)
        if block is not None:
            return False, f"拒绝写回候选：{block}", merged

        merged["outcome"] = "candidate"
        return True, "", merged

    def _prepare_event_context(self, query: str, *, force_infer: bool = False) -> str:
        """确定性激活上下文；miss 或强制刷新时在 agent 层显式触发事件采集。

        ADR-021：事件采集是 LLM 推理步骤，不再隐藏在上下文准备服务内部；
        本方法是该推理在本 agent 的显式触发点。
        """
        if not force_infer:
            text = self.context.prepare_context(query)
            if text:
                return text
        self._logger.info(f"[ToG] 上下文未命中，显式触发事件采集: {query[:80]}")
        self._event_inference.invoke({"query": query})
        return self.context.prepare_context(query)

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
            self._make_run_param_search_tool(),
            self._make_prove_causality_tool(),
            self._make_record_path_outcome_tool(),
        ]

    # ── 图工具 ─────────────────────────────────────────────────

    def _make_prepare_context_tool(self) -> Any:
        logger = self._logger
        monitoring = self._monitoring
        agent = self

        @tool
        def prepare_context(query: str, refresh_events: bool = False) -> str:
            """锚定研究问题：激活事件/知识子图；未命中时显式采集事件。

            Args:
                query: 研究意图或标的描述
                refresh_events: True 时强制再跑事件采集（即使已有激活结果）

            Returns:
                可注入后续推理的上下文摘要
            """
            with monitoring.track("research.prepare_context"):
                logger.info(f"[ToG] prepare_context: {query[:80]}")
                text = agent._prepare_event_context(query, force_infer=refresh_events)
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
                            node_name = p.node.label if p.node is not None else p.sid
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
                name: 算子名（snake_case）。必须名实一致：描述真实数据域+变换；
                    行情列用 return/price/vol/momentum 等词根，财务列才可用
                    roe/margin/earnings 等。禁止 roe_quality（实为价格）这类误导名。
                intent: 研发意图——写清真实输入列与计算公式，禁止用基本面叙事包装价格因子。
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
                from long_earn.backtest import (  # noqa: PLC0415
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
        agent = self

        @tool
        def run_backtest(strategy_yaml: str) -> str:
            """在训练集上跑回测——证据工具，不可用直觉替代。

            Args:
                strategy_yaml: 策略 YAML
            """
            with monitoring.track("research.run_backtest"):
                start = ctx.config.train_start_date
                end = ctx.config.train_end_date
                logger.info(f"[ToG] run_backtest: {start}~{end}")
                agent._register_trial(strategy_yaml)
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
                elif _evidence_metrics_block_reason(slim) is None:
                    agent._cache_backtest_evidence(strategy_yaml, slim)
                return json.dumps(slim, ensure_ascii=False, default=str)

        return run_backtest

    def _make_run_oos_gates_tool(self) -> Any:
        logger = self._logger
        monitoring = self._monitoring
        agent = self

        @tool
        def run_oos_gates(strategy_yaml: str) -> str:
            """Walk-Forward OOS 硬性门控 + DSR/PBO 诊断（ADR-022）。

            硬性：稳定性 + 相对 current best 合并阈值。
            诊断：DSR / PBO（缺料 skipped，不静默当通过）。

            Args:
                strategy_yaml: 策略 YAML
            """
            with monitoring.track("research.run_oos_gates"):
                logger.info("[ToG] run_oos_gates")
                return json.dumps(
                    agent._run_oos_gates_impl(strategy_yaml),
                    ensure_ascii=False,
                    default=str,
                )

        return run_oos_gates

    def _build_oos_hard_gate(
        self, fold_results: list[Any]
    ) -> tuple[Any, float | None, dict[str, Any], bool, list[str]]:
        """Walk-Forward 稳定性 + 合并门；返回稳定性、OOS 均值与硬性通过结论。"""
        from long_earn.strategy_optimization.overfit_gates import (  # noqa: PLC0415
            WalkForwardStabilityGate,
            evaluate_merge_gate,
            mean_fold_sharpe,
        )

        stability = WalkForwardStabilityGate().evaluate(fold_results)
        oos_mean = mean_fold_sharpe(fold_results)
        merge: dict[str, Any]
        if oos_mean is None:
            merge = {
                "passed": False,
                "reason": "无有效 OOS mean sharpe，无法评估合并门",
                "oos_mean_sharpe": None,
                "current_best": self._current_best_oos,
                "threshold": 0.05,
            }
        else:
            merge = evaluate_merge_gate(oos_mean, self._current_best_oos)

        hard_passed = bool(stability.passed and merge.get("passed"))
        reason_parts = [f"稳定性: {stability.reason}", f"合并: {merge['reason']}"]
        return stability, oos_mean, merge, hard_passed, reason_parts

    def _evaluate_pbo_diagnostic(
        self, train_bt: dict[str, Any], oos_mean: float | None
    ) -> dict[str, Any]:
        """登记本候选 IS/OOS 与收益列后评估 PBO 诊断。"""
        from long_earn.strategy_optimization.overfit_gates import (  # noqa: PLC0415
            _MIN_STRATEGIES_FOR_PBO,
            BacktestOverfitGate,
            diagnostic_to_dict,
        )

        is_sharpe = 0.0
        raw_is = train_bt.get("sharpe_ratio")
        if isinstance(raw_is, (int, float)):
            is_sharpe = float(raw_is)
        return_col = _daily_returns_as_floats(train_bt.get("daily_returns"))
        if return_col:
            self._oos_return_columns.append(return_col)
        if oos_mean is not None:
            self._oos_candidate_pairs.append((is_sharpe, float(oos_mean)))

        pbo_gate = BacktestOverfitGate()
        is_list = [p[0] for p in self._oos_candidate_pairs]
        oos_list = [p[1] for p in self._oos_candidate_pairs]
        n_cols = len(self._oos_return_columns)
        if n_cols >= _MIN_STRATEGIES_FOR_PBO:
            col_lens = [len(col) for col in self._oos_return_columns]
            if max(col_lens) - min(col_lens) > 0:
                pbo = pbo_gate.evaluate(is_list, oos_list)
            else:
                t_len = col_lens[0]
                matrix = [
                    [self._oos_return_columns[j][i] for j in range(n_cols)]
                    for i in range(t_len)
                ]
                pbo = pbo_gate.evaluate_returns_matrix(matrix, n_blocks=8)
                if pbo.status == "skipped":
                    pbo = pbo_gate.evaluate(is_list, oos_list)
        else:
            pbo = pbo_gate.evaluate(is_list, oos_list)

        return diagnostic_to_dict(
            status=pbo.status,
            reason=pbo.reason,
            pbo_probability=pbo.pbo_probability,
            n_strategies=pbo.n_strategies,
            n_samples=pbo.n_samples,
            method=_pbo_method_from_reason(pbo.reason),
        )

    def _run_oos_gates_impl(self, strategy_yaml: str) -> dict[str, Any]:
        """``run_oos_gates`` 确定性实现（ADR-022 §A）。"""
        from long_earn.strategy_optimization.acceptance import (  # noqa: PLC0415
            is_metrics_unreliable,
        )

        bt = self.context.backtest_service
        train_bt = bt.run(
            strategy_yaml=strategy_yaml,
            start_date=self.context.config.train_start_date,
            end_date=self.context.config.train_end_date,
        )
        self._register_trial(strategy_yaml)
        if isinstance(train_bt, dict) and is_metrics_unreliable(train_bt):
            diag = train_bt.get("strategy_diagnostics") or {}
            return {
                "passed": False,
                "reason": (
                    "训练集回测指标不可信"
                    f"（degenerate={diag.get('degenerate')}, "
                    f"factor_failures={diag.get('failed_factor_aliases')}, "
                    f"step_failures={diag.get('failed_step_labels')}）"
                    "，禁止进入 OOS 门"
                ),
                "metrics_unreliable": True,
            }

        try:
            oos = bt.run_oos(
                strategy_yaml=strategy_yaml,
                start_date=self.context.config.test_start_date,
                end_date=self.context.config.test_end_date,
                gap=5,
            )
        except Exception as exc:
            return {"passed": False, "error": f"OOS 执行失败: {exc}"}

        fold_results: list[Any] = []
        if isinstance(oos, dict):
            fold_results = oos.get("fold_results") or oos.get("folds") or []
            if not fold_results and "metrics" in oos:
                fold_results = [oos]

        stability, oos_mean, merge, hard_passed, reason_parts = (
            self._build_oos_hard_gate(fold_results)
        )

        train_dict = train_bt if isinstance(train_bt, dict) else {}
        dsr_payload = self._evaluate_dsr_diagnostic(
            train_bt=train_dict,
            fold_results=fold_results,
            oos_mean=oos_mean,
            worst_fold_sharpe=stability.worst_fold_sharpe,
            has_fold_sharpes=bool(stability.fold_sharpes),
        )
        pbo_payload = self._evaluate_pbo_diagnostic(train_dict, oos_mean)

        if hard_passed and oos_mean is not None:
            prev = self._current_best_oos
            if prev is None or oos_mean > prev:
                self._current_best_oos = oos_mean
                self._logger.info(
                    f"[ToG] 更新 current best OOS mean sharpe={oos_mean:.4f}"
                )

        payload: dict[str, Any] = {
            "passed": hard_passed,
            "reason": "；".join(reason_parts),
            "worst_fold_sharpe": stability.worst_fold_sharpe,
            "fold_sharpe_std": stability.fold_sharpe_std,
            "consistency_ratio": stability.consistency_ratio,
            "stability": {
                "passed": stability.passed,
                "reason": stability.reason,
            },
            "merge": merge,
            "dsr": dsr_payload,
            "pbo": pbo_payload,
            "n_eff_trials": self._n_eff_trials,
        }
        self._cache_oos_evidence(
            strategy_yaml,
            passed=hard_passed,
            metrics=payload,
            dsr_status=str(dsr_payload.get("status")),
        )
        return payload

    def _evaluate_dsr_diagnostic(
        self,
        *,
        train_bt: dict[str, Any],
        fold_results: list[Any],
        oos_mean: float | None,
        worst_fold_sharpe: float,
        has_fold_sharpes: bool,
    ) -> dict[str, Any]:
        """构造 DSR 诊断片段（可含完整 skew/kurt、MinTRL、haircut）。"""
        from long_earn.strategy_optimization.overfit_gates import (  # noqa: PLC0415
            _MIN_RETURNS_FOR_MOMENTS,
            DeflatedSharpeGate,
            daily_return_moments,
            diagnostic_to_dict,
            evaluate_haircut_sharpe,
            evaluate_mintrl,
        )

        if oos_mean is not None:
            observed_sharpe = float(oos_mean)
            observed_sharpe_source = "oos_mean"
        elif has_fold_sharpes:
            observed_sharpe = float(worst_fold_sharpe)
            observed_sharpe_source = "worst_fold"
        else:
            observed_sharpe = 0.0
            observed_sharpe_source = "worst_fold"

        n_obs = 252
        fold_days_resolved = False
        if fold_results:
            day_vals = [
                int(f.get("trading_days", 0) or 0)
                for f in fold_results
                if isinstance(f, dict)
            ]
            avg_days = sum(day_vals) / max(len(fold_results), 1)
            if avg_days > 0:
                n_obs = max(int(avg_days), 63)
                fold_days_resolved = True
            else:
                td = train_bt.get("trading_days")
                if isinstance(td, int) and td > 0:
                    n_obs = max(td, 63)
                    fold_days_resolved = True

        train_returns = train_bt.get("daily_returns")
        if not fold_days_resolved:
            train_values = _daily_returns_as_floats(train_returns)
            if len(train_values) >= _MIN_RETURNS_FOR_MOMENTS:
                n_obs = max(len(train_values), 63)
        moments = daily_return_moments(train_returns)
        moments_source = "train_daily_returns" if moments else "none"
        skew = moments[0] if moments else None
        kurt = moments[1] if moments else None
        dsr = DeflatedSharpeGate().evaluate(
            observed_sharpe=observed_sharpe,
            n_trials=self._n_eff_trials,
            n_observations=n_obs,
            skew=skew,
            kurtosis=kurt,
        )
        mintrl_skew = skew if skew is not None else 0.0
        mintrl_kurt = kurt if kurt is not None else 3.0
        payload = diagnostic_to_dict(
            status=dsr.status,
            reason=dsr.reason,
            simplified=dsr.simplified,
            t_statistic=dsr.t_statistic,
            n_trials=self._n_eff_trials,
            n_observations=n_obs,
            moments_source=moments_source,
            observed_sharpe_source=observed_sharpe_source,
            mintrl=evaluate_mintrl(
                observed_sharpe=observed_sharpe,
                n_observations=n_obs,
                skew=mintrl_skew,
                kurtosis=mintrl_kurt,
            ),
            haircut=evaluate_haircut_sharpe(
                observed_sharpe=observed_sharpe,
                n_trials=self._n_eff_trials,
                n_observations=n_obs,
            ),
        )
        return payload

    def _make_run_param_search_tool(self) -> Any:
        """参数网格搜索工具 — 在训练集上暴力搜索最优参数组合。

        利用 ParamGrid + ParallelRunner 基建，对策略模板的 {{ var }} 占位符
        做笛卡尔积展开，并行回测所有组合，返回 Top-K 最优结果。
        """
        logger = self._logger
        monitoring = self._monitoring
        ctx = self.context
        agent = self

        @tool
        def run_param_search(
            strategy_template: str,
            param_grid_json: str,
        ) -> str:
            """参数网格搜索：在训练集上暴力搜索最优参数组合。

            Args:
                strategy_template: 策略 YAML 模板，使用 {{ var }} 作为参数占位符
                param_grid_json: JSON 字符串，格式为
                    {"scalars": {"param1": [v1, v2], ...}, "structs": {"key": [v1, v2], ...}}
            """
            with monitoring.track("research.run_param_search"):
                # 解析参数网格
                try:
                    grid_dict: dict[str, Any] = json.loads(param_grid_json)
                except json.JSONDecodeError as exc:
                    return json.dumps(
                        {"error": f"param_grid_json 解析失败: {exc}"},
                        ensure_ascii=False,
                    )

                from long_earn.backtest import (  # noqa: PLC0415
                    _MAX_GRID_DEFAULT,
                    ParamGrid,
                )

                param_grid = ParamGrid(
                    scalars=grid_dict.get("scalars", {}),
                    structs=grid_dict.get("structs", {}),
                )
                total = param_grid.total_combinations
                if total == 0:
                    return json.dumps(
                        {"error": "参数网格为空，请提供至少一个参数维度"},
                        ensure_ascii=False,
                    )
                if total > _MAX_GRID_DEFAULT:
                    return json.dumps(
                        {
                            "error": (
                                f"参数组合 {total} 超过上限 {_MAX_GRID_DEFAULT}，"
                                "请缩小参数范围或减少维度"
                            )
                        },
                        ensure_ascii=False,
                    )

                agent._strategy_trial_count += total
                base_fp = _strategy_fingerprint(strategy_template)
                for i in range(total):
                    agent._trial_fingerprints.add(f"{base_fp}:grid:{i}")
                logger.info(
                    f"[ToG] run_param_search: {total} 组合，"
                    f"训练集 {ctx.config.train_start_date}~{ctx.config.train_end_date}"
                )

                try:
                    result = ctx.backtest_service.run_grid(
                        strategy_template=strategy_template,
                        param_grid=param_grid,
                        start_date=ctx.config.train_start_date,
                        end_date=ctx.config.train_end_date,
                    )
                except Exception as exc:
                    return json.dumps(
                        {"error": f"网格搜索执行失败: {exc}"},
                        ensure_ascii=False,
                    )

                outcomes = result.get("outcomes", [])
                reliable = [o for o in outcomes if o.get("success")]
                if not reliable:
                    return json.dumps(
                        {
                            "error": "所有参数组合回测均失败",
                            "total": result.get("total"),
                            "failure_count": result.get("failure_count"),
                        },
                        ensure_ascii=False,
                    )

                # 按 sharpe 排序取 Top-5
                sorted_outcomes = sorted(
                    reliable, key=lambda o: o.get("sharpe_ratio", -999), reverse=True
                )
                top_k = sorted_outcomes[:5]

                summary = {
                    "total_combinations": total,
                    "success_count": result.get("success_count"),
                    "failure_count": result.get("failure_count"),
                    "best_sharpe": result.get("best_sharpe"),
                    "best_return": result.get("best_return"),
                    "best_param_desc": result.get("best_param_desc"),
                    "top_results": [
                        {
                            "rank": i + 1,
                            "sharpe_ratio": o.get("sharpe_ratio"),
                            "total_return": o.get("total_return"),
                            "max_drawdown": o.get("max_drawdown"),
                            "param_desc": o.get("param_desc"),
                        }
                        for i, o in enumerate(top_k)
                    ],
                }
                return json.dumps(summary, ensure_ascii=False)

        return run_param_search

    def _make_prove_causality_tool(self) -> Any:
        monitoring = self._monitoring

        @tool
        def prove_causality(operator_name: str) -> str:
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

        return prove_causality

    def _make_record_path_outcome_tool(self) -> Any:
        logger = self._logger
        monitoring = self._monitoring
        ctx = self.context
        agent = self

        @tool
        def record_path_outcome(
            path_summary: str,
            strategy_yaml: str = "",
            metrics_json: str = "",
            reflection: str = "",
            outcome: str = "success",
        ) -> str:
            """将探索路径结果写回 Substance（飞轮）。

            Args:
                path_summary: 路径摘要 / 策略名
                strategy_yaml: 策略 YAML（success/candidate 时必填以核对证据）
                metrics_json: 指标 JSON 字符串
                reflection: 反思文本
                outcome: ``success`` / ``failure`` / ``candidate``（大小写不敏感）。
                    success 须 OOS 硬性门控通过；candidate 允许仅有训练集证据。
            """
            with monitoring.track("research.record_path_outcome"):
                logger.info(
                    f"[ToG] record_path_outcome: {path_summary[:80]} outcome={outcome}"
                )
                metrics: dict[str, Any] = {}
                if metrics_json.strip():
                    try:
                        metrics = json.loads(metrics_json)
                    except json.JSONDecodeError:
                        metrics = {"raw": metrics_json[:500]}

                outcome_norm = outcome.strip().lower()
                if outcome_norm == "failure":
                    metrics["outcome"] = "failure"
                elif outcome_norm == "candidate":
                    allowed, err, metrics = agent._validate_candidate_writeback(
                        strategy_yaml, metrics
                    )
                    if not allowed:
                        return err
                else:
                    allowed, err, metrics = agent._validate_success_writeback(
                        strategy_yaml, metrics
                    )
                    if not allowed:
                        return err

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
        self._evidence_cache = {}
        self._oos_return_columns = []
        self._oos_candidate_pairs = []
        self._current_best_oos = None
        query = idea if not constraints else f"{idea} (约束: {constraints})"
        # 入口自动准备上下文（miss 时显式触发事件采集，见 _prepare_event_context）
        try:
            self._last_context = self._prepare_event_context(query)
        except Exception as exc:
            self._logger.warning(f"上下文准备失败（非致命）: {exc}")
            self._last_context = ""

        user_content = query
        if self._last_context:
            user_content = f"{query}\n\n## 已激活上下文\n\n{self._last_context[:3000]}"

        run_config: RunnableConfig = {"recursion_limit": _DEFAULT_RECURSION_LIMIT}
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
