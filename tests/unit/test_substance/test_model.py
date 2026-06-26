"""Substance 模型测试 — 核心行为：sid 生成 + 可见性 + 衰减。"""

from datetime import datetime, timedelta

from long_earn.substance.model import Substance, SubstanceForm


def test_sid_auto_generated():
    s = Substance(form=SubstanceForm.KNOWLEDGE, content="测试")
    assert s.sid.startswith("sub_")


def test_visibility_and_decay():
    """可见性（visible_from + expires_at）+ 衰减因子半衰期验证。"""
    now = datetime.now()
    future = now + timedelta(days=1)
    past = now - timedelta(days=1)

    visible = Substance(form=SubstanceForm.EVENT, content="已公开")
    assert visible.is_visible_at(now)

    future_s = Substance(form=SubstanceForm.EVENT, content="未来", visible_from=future)
    assert not future_s.is_visible_at(now)
    assert future_s.is_visible_at(future + timedelta(hours=1))

    expired = Substance(form=SubstanceForm.EVENT, content="过期", expires_at=past)
    assert not expired.is_visible_at(now)

    aged = Substance(
        form=SubstanceForm.KNOWLEDGE,
        content="旧知识",
        created_at=now - timedelta(days=90),
        decay_half_life_days=90.0,
    )
    assert 0.4 < aged.decay_factor() < 0.6  # 半衰期后约 0.5
