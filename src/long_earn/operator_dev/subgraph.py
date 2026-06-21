"""算子开发子图 —— LangGraph 编排。

拓扑::

    START
      ↓
    pick_task ──(backlog 空)──► END
      ↓
    spec_review ──(reject)──► (循环回 pick_task)
      ↓ (accept)
    implement
      ↓
    test_validate ──(失败, 预算未用尽)──► refine ─┐
      │                                          │
      └─(失败, 预算用尽)──► mark_blocked ────────┤
      ↓ (通过)                                  │
    register ───────────────────────────────────┤
      ↓                                        │
    (循环回 pick_task) ◄────────────────────────┘

关键性质：
- test_validate 的"无未来函数"由 :func:`prove_causality` 数值证明，是算子上线
  的硬约束（与策略层 visibility guard 的因果性同源）。
- LLM 代码风险经 sandbox AST 审计 + 隔离编译收敛在本子图。
- 策略研发永不阻塞：本子图异步消费 backlog。

为支持确定性 e2e（不依赖真实 LLM），:class:`OperatorImplementer` 可注入
（如 :class:`FakeImplementer`）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

import polars as pl
from langgraph.graph import END, START, StateGraph

from long_earn.backtest.operators import OPERATOR_REGISTRY, register_operator
from long_earn.backtest.operators.causality import prove_causality
from long_earn.operator_dev.agents import LLMImplementer, OperatorImplementer
from long_earn.operator_dev.backlog import OperatorBacklog
from long_earn.operator_dev.sandbox import OperatorLoadError, load_operator_class
from long_earn.operator_dev.spec import OperatorSpec
from long_earn.operator_dev.state import OperatorDevState

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext
    from long_earn.services import LoggerService

MAX_OP_REFINES = 3


class _SilentLogger:
    """无 context 时的空日志实现。"""

    def debug(self, message: str) -> None: ...
    def info(self, message: str) -> None: ...
    def warning(self, message: str) -> None: ...
    def error(self, message: str) -> None: ...
    def exception(self, message: str) -> None: ...


_SILENT_LOGGER = _SilentLogger()


def _make_causality_panel() -> pl.DataFrame:
    """构造确定性面板供因果性证明：3 symbol × 30 日。"""
    rows = []
    base = datetime(2024, 1, 1)
    for i in range(30):
        ts = base + timedelta(days=i)
        for s_idx, sym in enumerate(["A.SZ", "B.SH", "C.SZ"]):
            t = i + 1
            close = 10.0 + s_idx * 3 + 0.4 * t + (t % 5) - 0.02 * (t % 11)
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": sym,
                    "close": round(close, 4),
                    "high": close + 0.2,
                    "low": close - 0.2,
                    "volume": 1000.0 * t,
                }
            )
    return pl.DataFrame(rows)


def _append_result(
    state: OperatorDevState, name: str, status: str, detail: str
) -> list[dict[str, Any]]:
    """把一条 spec 处理结果累计进 results（last-wins reducer 下需返回完整列表）。"""
    results = list(state.get("results", []))
    results.append({"name": name, "status": status, "detail": detail})
    return results


# ── 节点 ────────────────────────────────────────────────────────────────


def _pick_task_node(
    state: OperatorDevState,  # noqa: ARG001
    backlog: OperatorBacklog,
    logger: LoggerService,
) -> dict:
    """取下一个 pending spec；空则结束。"""
    spec = backlog.pick_next()
    if spec is None:
        return {"current_spec": None}
    logger.info(f"[op_dev] 取出任务: {spec.name} ({spec.category})")
    return {"current_spec": spec, "refine_count": 0, "failure_report": ""}


def _spec_review_node(
    state: OperatorDevState,
    backlog: OperatorBacklog,
    logger: LoggerService,
) -> dict:
    """去重 + 合理性 + reference_strategy 非空校验。"""
    spec: OperatorSpec | None = state.get("current_spec")
    if spec is None:
        return {}

    # 去重：目录已有同名算子 → 直接 resolved
    if spec.name in OPERATOR_REGISTRY:
        logger.warning(f"[op_dev] {spec.name} 目录已存在，跳过")
        backlog.update_status(spec.name, "resolved")
        return {
            "current_spec": None,
            "results": _append_result(state, spec.name, "resolved", "目录已存在"),
        }
    # reference_strategy 非空由 OperatorSpec.__post_init__ 保证；额外做基本合理性
    if not spec.input_fields:
        logger.warning(f"[op_dev] {spec.name} input_fields 为空，拒绝")
        backlog.update_status(spec.name, "resolved")
        return {
            "current_spec": None,
            "results": _append_result(
                state, spec.name, "rejected", "input_fields 为空"
            ),
        }
    return {}


def _implement_node(
    state: OperatorDevState,
    implementer: OperatorImplementer,
    logger: LoggerService,
) -> dict:
    spec = state.get("current_spec")
    if spec is None:
        return {}
    source = implementer.implement(spec)
    logger.info(f"[op_dev] {spec.name} 实现完成，源码 {len(source)} 字符")
    return {"source_code": source, "failure_report": ""}


def _test_and_validate_node(
    state: OperatorDevState,
    backlog: OperatorBacklog,  # noqa: ARG001
    logger: LoggerService,
) -> dict:
    """合并 test + validate：审计 + 加载 + 契约 + 因果性证明。

    全部通过 → code_ready=True；任一失败 → code_ready=False + failure_report。
    因果性证明是"数学证明无未来函数"的硬关卡。
    """
    spec = state.get("current_spec")
    source = state.get("source_code", "")
    if spec is None:
        return {}

    # 1) 审计 + 加载 + 契约
    try:
        cls = load_operator_class(source, expected_name=spec.name)
    except OperatorLoadError as exc:
        report = f"审计/加载失败: {exc}"
        logger.warning(f"[op_dev] {spec.name} {report}")
        return {"code_ready": False, "failure_report": report}

    # 2) 因果性证明（无未来函数）—— 用实例化后的算子在确定性面板上证明
    try:
        instance = cls()
        params = cls.params_cls()
    except Exception as exc:
        report = f"算子实例化/参数构造失败: {type(exc).__name__}: {exc}"
        return {"code_ready": False, "failure_report": report}

    try:
        reports = prove_causality(instance, params, _make_causality_panel())
    except Exception as exc:
        report = f"因果性证明执行异常: {type(exc).__name__}: {exc}"
        return {"code_ready": False, "failure_report": report}

    failed = [r for r in reports if not r.passed]
    if failed:
        detail = "; ".join(f"T={r.split_timestamp}: {r.detail}" for r in failed)
        report = f"因果性证明失败（含未来函数）: {detail}"
        logger.error(f"[op_dev] {spec.name} {report}")
        return {"code_ready": False, "failure_report": report}

    logger.info(f"[op_dev] {spec.name} 审计+契约+因果性全部通过")
    return {"code_ready": True, "failure_report": ""}


def _refine_node(
    state: OperatorDevState,
    implementer: OperatorImplementer,
    logger: LoggerService,
) -> dict:
    spec = state.get("current_spec")
    if spec is None:
        return {}
    new_source = implementer.refine(spec, state.get("failure_report", ""))
    refine_count = state.get("refine_count", 0) + 1
    logger.info(f"[op_dev] {spec.name} 第 {refine_count} 次修复")
    # 保留 failure_report 供 mark_blocked 记录；test_validate 会按新源码重判
    return {
        "source_code": new_source,
        "refine_count": refine_count,
        "code_ready": False,
    }


def _register_node(
    state: OperatorDevState,
    backlog: OperatorBacklog,
    logger: LoggerService,
) -> dict:
    spec = state.get("current_spec")
    source = state.get("source_code", "")
    if spec is None:
        return {}
    instance = load_operator_class(source, expected_name=spec.name)()
    register_operator(instance)
    backlog.update_status(spec.name, "registered")
    logger.info(f"[op_dev] {spec.name} 已注册上线")
    registered = [*state.get("registered_names", []), spec.name]
    return {
        "registered_names": registered,
        "results": _append_result(state, spec.name, "registered", "ok"),
        "current_spec": None,
    }


def _mark_blocked_node(
    state: OperatorDevState,
    backlog: OperatorBacklog,
    logger: LoggerService,
) -> dict:
    spec = state.get("current_spec")
    if spec is None:
        return {}
    backlog.update_status(spec.name, "blocked")
    logger.error(
        f"[op_dev] {spec.name} 修复预算用尽，标记 blocked: "
        f"{state.get('failure_report', '')}"
    )
    return {
        "results": _append_result(
            state, spec.name, "blocked", state.get("failure_report", "")
        ),
        "current_spec": None,
    }


# ── 条件路由 ────────────────────────────────────────────────────────────


def _after_pick_cond(state: OperatorDevState) -> str:
    return "end" if state.get("current_spec") is None else "spec_review"


def _after_review_cond(state: OperatorDevState) -> str:
    return "implement" if state.get("current_spec") is not None else "pick_task"


def _after_test_cond(state: OperatorDevState) -> str:
    if state.get("code_ready"):
        return "register"
    if state.get("refine_count", 0) >= MAX_OP_REFINES:
        return "mark_blocked"
    return "refine"


# ── 子图构造 ────────────────────────────────────────────────────────────


def create_operator_dev_subgraph(
    context: RuntimeContext | None = None,
    *,
    implementer: OperatorImplementer | None = None,
    backlog: OperatorBacklog | None = None,
) -> Any:
    """创建算子开发子图。

    Args:
        context: 运行时上下文；若 ``implementer`` 未提供则用它构造 LLMImplementer。
        implementer: 算子实现者（生产 LLM / 测试 Fake）。可注入以解耦 LLM。
        backlog: 算子缺口队列；不传则用空内存 backlog（由调用方 submit）。
    """

    if implementer is None:
        if context is None:
            raise ValueError("需提供 context 或 implementer")
        implementer = LLMImplementer(context)
    if backlog is None:
        backlog = OperatorBacklog()

    log: LoggerService = context.logger if context is not None else _SILENT_LOGGER  # type: ignore[assignment]
    workflow = StateGraph(OperatorDevState)

    workflow.add_node(
        "pick_task", partial(_pick_task_node, backlog=backlog, logger=log)
    )
    workflow.add_node(
        "spec_review", partial(_spec_review_node, backlog=backlog, logger=log)
    )
    workflow.add_node(
        "implement", partial(_implement_node, implementer=implementer, logger=log)
    )
    workflow.add_node(
        "test_validate",
        partial(_test_and_validate_node, backlog=backlog, logger=log),
    )
    workflow.add_node(
        "refine", partial(_refine_node, implementer=implementer, logger=log)
    )
    workflow.add_node("register", partial(_register_node, backlog=backlog, logger=log))
    workflow.add_node(
        "mark_blocked", partial(_mark_blocked_node, backlog=backlog, logger=log)
    )

    workflow.add_edge(START, "pick_task")
    workflow.add_conditional_edges(
        "pick_task", _after_pick_cond, {"end": END, "spec_review": "spec_review"}
    )
    workflow.add_conditional_edges(
        "spec_review",
        _after_review_cond,
        {"implement": "implement", "pick_task": "pick_task"},
    )
    workflow.add_edge("implement", "test_validate")
    workflow.add_conditional_edges(
        "test_validate",
        _after_test_cond,
        {"register": "register", "refine": "refine", "mark_blocked": "mark_blocked"},
    )
    workflow.add_edge("refine", "test_validate")
    # register / mark_blocked 无条件回到 pick_task 取下一条
    workflow.add_edge("register", "pick_task")
    workflow.add_edge("mark_blocked", "pick_task")

    return workflow.compile()
