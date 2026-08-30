"""知识缺口枚举 — DualGraph 风格的确定性下一轮任务（ADR-023）。

只读 SubstanceStore 与可选会话快照，不调用语言模型。
输出供 ResearchAgent ``list_gaps`` 工具注入 prompt。
"""

from __future__ import annotations

from dataclasses import dataclass

from long_earn.substance.model import ReviewStatus, Substance, SubstanceForm
from long_earn.substance.store import SubstanceStore

_MAX_PER_KIND = 8

RELATION_CONTRADICTS = "contradicts"
RELATION_TESTS = "tests"


@dataclass(frozen=True)
class KnowledgeGap:
    """一条可执行的知识缺口。"""

    kind: str
    sid: str
    summary: str
    suggested_action: str


@dataclass(frozen=True)
class BeamPathSnapshot:
    """单次 ToG invoke 内的 beam 路径快照。"""

    path_id: str
    entity: str
    status: str


@dataclass(frozen=True)
class SessionExploreState:
    """进程内探索状态 — 不写盘，随 invoke 结束。"""

    beams: tuple[BeamPathSnapshot, ...] = ()
    train_only_hashes: tuple[str, ...] = ()


def collect_store_gaps(store: SubstanceStore) -> list[KnowledgeGap]:
    """从持久化物质图收集缺口（矛盾、缺证据、未过门候选）。"""
    substances = store.get_all()
    by_sid = {s.sid: s for s in substances}
    tested_sids = {
        s.source_id
        for s in substances
        if s.form is SubstanceForm.RELATION
        and (s.relation_type or "") == RELATION_TESTS
        and s.source_id
    }
    gaps: list[KnowledgeGap] = []
    gaps.extend(_contradiction_gaps(substances, by_sid))
    gaps.extend(_event_evidence_gaps(substances))
    gaps.extend(_staging_gaps(substances, tested_sids))
    gaps.extend(_train_only_strategy_gaps(substances))
    return gaps


def collect_session_gaps(session: SessionExploreState) -> list[KnowledgeGap]:
    """从本次 ToG 会话收集未 OOS 的 beam 与仅训练集指纹。"""
    gaps: list[KnowledgeGap] = []
    untested = [
        b for b in session.beams if b.status in {"open", "active"} and b.path_id
    ]
    for beam in untested[:_MAX_PER_KIND]:
        gaps.append(
            KnowledgeGap(
                kind="untested_beam",
                sid=beam.path_id,
                summary=f"路径 {beam.path_id}（{beam.entity}）尚未 run_oos_gates",
                suggested_action="对该路径编译 YAML 后调用 run_oos_gates，或标 failure",
            )
        )
    for fp in session.train_only_hashes[:_MAX_PER_KIND]:
        gaps.append(
            KnowledgeGap(
                kind="train_only_session",
                sid=fp,
                summary=f"策略指纹 {fp} 仅有训练集证据",
                suggested_action="调用 run_oos_gates；写回时 outcome=candidate 而非 success",
            )
        )
    return gaps


def collect_gaps(
    store: SubstanceStore,
    session: SessionExploreState | None = None,
) -> list[KnowledgeGap]:
    """合并持久化缺口与会话缺口。"""
    gaps = collect_store_gaps(store)
    if session is not None:
        gaps.extend(collect_session_gaps(session))
    return gaps


def format_gaps(gaps: list[KnowledgeGap]) -> str:
    """格式化为 Agent 可读清单；无缺口时给出明确空结果。"""
    if not gaps:
        return "无知识缺口。"
    lines = [f"知识缺口 {len(gaps)} 条（确定性扫描，非模型生成）："]
    for i, gap in enumerate(gaps, 1):
        lines.append(
            f"{i}. [{gap.kind}] {gap.summary} | 建议: {gap.suggested_action} | sid={gap.sid}"
        )
    return "\n".join(lines)


def _contradiction_gaps(
    substances: list[Substance],
    by_sid: dict[str, Substance],
) -> list[KnowledgeGap]:
    gaps: list[KnowledgeGap] = []
    for s in substances:
        if s.form is not SubstanceForm.RELATION:
            continue
        if (s.relation_type or "") != RELATION_CONTRADICTS:
            continue
        if s.metadata.get("adjudicated"):
            continue
        left = by_sid.get(s.source_id or "")
        right = by_sid.get(s.target_id or "")
        left_text = left.content[:80] if left is not None else (s.source_id or "")
        right_text = right.content[:80] if right is not None else (s.target_id or "")
        gaps.append(
            KnowledgeGap(
                kind="open_contradiction",
                sid=s.sid,
                summary=f"未裁决矛盾: 「{left_text}」⊥「{right_text}」",
                suggested_action="两侧都保留；用回测或第二源裁决，禁止覆盖任一方",
            )
        )
        if len(gaps) >= _MAX_PER_KIND:
            break
    return gaps


def _event_evidence_gaps(substances: list[Substance]) -> list[KnowledgeGap]:
    gaps: list[KnowledgeGap] = []
    for s in substances:
        if s.form is not SubstanceForm.EVENT:
            continue
        if s.review_status is ReviewStatus.RAW:
            continue
        claim = s.metadata.get("claim")
        evidence = ""
        if isinstance(claim, dict):
            evidence = str(claim.get("evidence_ref") or "")
        if evidence:
            continue
        gaps.append(
            KnowledgeGap(
                kind="event_missing_evidence",
                sid=s.sid,
                summary=f"事件断言无证据指针: {s.content[:80]}",
                suggested_action="补 evidence_ref 指向采集原文 sid/url，或降为不可激活",
            )
        )
        if len(gaps) >= _MAX_PER_KIND:
            break
    return gaps


def _staging_gaps(
    substances: list[Substance],
    tested_sids: set[str],
) -> list[KnowledgeGap]:
    gaps: list[KnowledgeGap] = []
    for s in substances:
        if s.review_status is not ReviewStatus.STAGING:
            continue
        if s.form is SubstanceForm.EVENT and s.sid in tested_sids:
            continue
        gaps.append(
            KnowledgeGap(
                kind="staging_unverified",
                sid=s.sid,
                summary=f"暂存未升格 ({s.form.value}): {s.content[:80]}",
                suggested_action="显式 activate include_staging 审查；过门后升格 committed",
            )
        )
        if len(gaps) >= _MAX_PER_KIND:
            break
    return gaps


def _train_only_strategy_gaps(substances: list[Substance]) -> list[KnowledgeGap]:
    gaps: list[KnowledgeGap] = []
    for s in substances:
        if s.form is not SubstanceForm.STRATEGY:
            continue
        metrics = s.metadata.get("backtest_metrics") or {}
        outcome = str(metrics.get("outcome") or s.metadata.get("outcome") or "").lower()
        if outcome != "candidate" and s.review_status is not ReviewStatus.STAGING:
            continue
        if outcome == "success":
            continue
        gaps.append(
            KnowledgeGap(
                kind="train_only_strategy",
                sid=s.sid,
                summary=f"策略经验仅候选/暂存: {s.content[:80]}",
                suggested_action="run_oos_gates；通过前不得 record_path_outcome success",
            )
        )
        if len(gaps) >= _MAX_PER_KIND:
            break
    return gaps
