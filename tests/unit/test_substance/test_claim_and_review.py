"""Claim / ReviewStatus 核心契约。"""

from datetime import datetime, timedelta

import pytest

from long_earn.substance.model import (
    Claim,
    ReviewStatus,
    Substance,
    SubstanceForm,
)
from long_earn.substance.motion import activate
from long_earn.substance.store import SubstanceStore


def test_claim_from_event_dict_fallback():
    claim = Claim.from_event_dict(
        {
            "content": "央行降准",
            "symbols": ["600519.SH"],
            "category": "政策",
        },
        evidence_ref="sub_raw_1",
    )
    assert claim.subject == "央行降准"
    assert claim.predicate == "impacts"
    assert claim.object == "600519.SH"
    assert claim.evidence_ref == "sub_raw_1"


def test_default_review_status_is_committed():
    s = Substance(form=SubstanceForm.KNOWLEDGE, content="存量")
    assert s.review_status is ReviewStatus.COMMITTED
    assert s.is_activatable() is True


def test_staging_excluded_unless_opt_in():
    store = SubstanceStore()
    store.add(
        Substance(
            form=SubstanceForm.EVENT,
            content="未过门新闻",
            keys=["茅台"],
            review_status=ReviewStatus.STAGING,
        )
    )
    assert activate("茅台", store, budget=10) == []
    staged = activate("茅台", store, budget=10, include_staging=True)
    assert len(staged) == 1


def test_raw_never_activates():
    store = SubstanceStore()
    store.add(
        Substance(
            form=SubstanceForm.KNOWLEDGE,
            content="原文段落",
            keys=["茅台"],
            review_status=ReviewStatus.RAW,
        )
    )
    assert activate("茅台", store, budget=10, include_staging=True) == []


def test_raw_cannot_be_overwritten():
    store = SubstanceStore()
    raw = Substance(
        form=SubstanceForm.KNOWLEDGE,
        content="原始证据",
        review_status=ReviewStatus.RAW,
    )
    store.add(raw)
    with pytest.raises(ValueError, match="RAW"):
        store.update(raw)
    clone = Substance(
        sid=raw.sid,
        form=SubstanceForm.KNOWLEDGE,
        content="篡改",
        review_status=ReviewStatus.RAW,
    )
    with pytest.raises(ValueError, match="RAW"):
        store.add(clone)


def test_future_event_still_time_filtered():
    now = datetime.now()
    store = SubstanceStore()
    store.add(
        Substance(
            form=SubstanceForm.EVENT,
            content="未来",
            keys=["事件"],
            visible_from=now + timedelta(days=1),
            review_status=ReviewStatus.COMMITTED,
        )
    )
    assert activate("事件", store, budget=10) == []
