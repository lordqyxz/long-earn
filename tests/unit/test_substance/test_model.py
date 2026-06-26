"""Substance 模型单元测试 — SubstanceForm / FilterLogic / Substance 基础行为。"""

from datetime import datetime, timedelta

from long_earn.substance.model import FilterLogic, Substance, SubstanceForm


class TestSubstanceModel:
    def test_default_sid_generated(self):
        s = Substance(form=SubstanceForm.KNOWLEDGE, content="测试")
        assert s.sid.startswith("sub_")
        assert s.form is SubstanceForm.KNOWLEDGE

    def test_is_visible_at_no_restrictions(self):
        s = Substance(form=SubstanceForm.EVENT, content="新闻")
        assert s.is_visible_at(datetime.now())

    def test_is_visible_at_with_visible_from(self):
        future = datetime.now() + timedelta(days=1)
        s = Substance(form=SubstanceForm.EVENT, content="未来事件", visible_from=future)
        assert not s.is_visible_at(datetime.now())
        assert s.is_visible_at(future + timedelta(hours=1))

    def test_is_visible_at_with_expires_at(self):
        past = datetime.now() - timedelta(days=1)
        s = Substance(form=SubstanceForm.EVENT, content="过期事件", expires_at=past)
        assert not s.is_visible_at(datetime.now())

    def test_decay_factor_fresh_is_one(self):
        s = Substance(form=SubstanceForm.KNOWLEDGE, content="新知识")
        assert abs(s.decay_factor() - 1.0) < 1e-6

    def test_decay_factor_aged(self):
        old = datetime.now() - timedelta(days=90)
        s = Substance(
            form=SubstanceForm.KNOWLEDGE,
            content="旧知识",
            created_at=old,
            decay_half_life_days=90.0,
        )
        factor = s.decay_factor()
        assert 0.4 < factor < 0.6  # 半衰期后约 0.5

    def test_form_is_str_enum(self):
        assert SubstanceForm.EVENT == "event"
        assert FilterLogic.AND_ANY == "and_any"
