"""Motion 层单元测试 — activate / decay / detect_conflicts / compress 接口契约。"""

from datetime import datetime, timedelta

from long_earn.substance.model import Substance, SubstanceForm
from long_earn.substance.motion import activate, compress, decay, detect_conflicts
from long_earn.substance.store import SubstanceStore


class TestActivate:
    def test_keyword_activation(self):
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="动量策略根据近期涨幅选股",
                keys=["动量", "涨幅"],
            )
        )
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="均值回归策略",
                keys=["均值回归"],
            )
        )
        activated = activate("动量选股", store, budget=10)
        assert len(activated) >= 1
        assert any("动量" in s.content for s in activated)

    def test_budget_truncation(self):
        store = SubstanceStore()
        for i in range(10):
            store.add(
                Substance(
                    form=SubstanceForm.KNOWLEDGE,
                    content=f"策略 {i}",
                    keys=["策略"],
                    insertion_order=i,
                )
            )
        activated = activate("策略", store, budget=3)
        assert len(activated) == 3


class TestDecay:
    def test_decay_marks_old_substances(self):
        store = SubstanceStore()
        store.add(Substance(form=SubstanceForm.EVENT, content="新事件"))
        old = Substance(
            form=SubstanceForm.EVENT,
            content="旧事件",
            created_at=datetime.now() - timedelta(days=30),
        )
        store.add(old)
        decayed = decay(store)
        assert decayed >= 1
        assert old.metadata.get("decayed") is True


class TestDetectConflicts:
    def test_no_conflict_for_unrelated(self):
        store = SubstanceStore()
        store.add(Substance(form=SubstanceForm.KNOWLEDGE, content="动量策略选股"))
        new = Substance(form=SubstanceForm.KNOWLEDGE, content="Python 编程教程")
        conflicts = detect_conflicts(store, new, min_similarity=0.5)
        assert len(conflicts) == 0

    def test_conflict_for_contradictory(self):
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="this strategy works very well 利好买入",
            )
        )
        new = Substance(
            form=SubstanceForm.KNOWLEDGE,
            content="this strategy works very bad 利空卖出",
        )
        conflicts = detect_conflicts(store, new, min_similarity=0.3)
        assert len(conflicts) >= 1


class TestCompress:
    def test_compress_no_similar(self):
        store = SubstanceStore()
        store.add(Substance(form=SubstanceForm.KNOWLEDGE, content="动量策略选股"))
        store.add(Substance(form=SubstanceForm.KNOWLEDGE, content="Python 编程教程"))
        store.add(Substance(form=SubstanceForm.KNOWLEDGE, content="数据分析方法"))
        removed = compress(store, min_similarity=0.9)
        assert removed == 0
        assert store.count == 3

    def test_compress_similar(self):
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="momentum strategy 动量策略根据近期涨幅",
            )
        )
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="momentum strategy 动量策略根据过去涨幅",
            )
        )
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="mean reversion 均值回归策略",
            )
        )
        removed = compress(store, min_similarity=0.3)
        assert removed > 0
        assert store.count < 3
