"""确定性知识缺口扫描（ADR-023）。"""

from long_earn.ontology.model import RelationType
from long_earn.substance.gaps import (
    BeamPathSnapshot,
    SessionExploreState,
    collect_gaps,
    format_gaps,
)
from long_earn.substance.model import ReviewStatus, Substance, SubstanceForm
from long_earn.substance.store import SubstanceStore


def test_collect_open_contradiction_and_missing_evidence():
    store = SubstanceStore()
    e1 = Substance(
        form=SubstanceForm.EVENT,
        content="利好",
        keys=["茅台"],
        review_status=ReviewStatus.STAGING,
        metadata={"claim": {"subject": "利好", "evidence_ref": ""}},
    )
    e2 = Substance(
        form=SubstanceForm.EVENT,
        content="利空",
        keys=["茅台"],
        review_status=ReviewStatus.STAGING,
        metadata={"claim": {"subject": "利空", "evidence_ref": "sub_raw"}},
    )
    store.add(e1)
    store.add(e2)
    store.add(
        Substance(
            form=SubstanceForm.RELATION,
            content="矛盾",
            source_id=e1.sid,
            target_id=e2.sid,
            relation_type=RelationType.CONTRADICTS.value,
            metadata={"adjudicated": False},
        )
    )
    gaps = collect_gaps(store)
    kinds = {g.kind for g in gaps}
    assert "open_contradiction" in kinds
    assert "event_missing_evidence" in kinds
    assert "staging_unverified" in kinds
    text = format_gaps(gaps)
    assert "知识缺口" in text


def test_session_untested_beam():
    store = SubstanceStore()
    session = SessionExploreState(
        beams=(BeamPathSnapshot(path_id="path_0", entity="动量", status="active"),),
        train_only_hashes=("abc123",),
    )
    gaps = collect_gaps(store, session)
    kinds = {g.kind for g in gaps}
    assert "untested_beam" in kinds
    assert "train_only_session" in kinds


def test_format_empty():
    assert format_gaps([]) == "无知识缺口。"
