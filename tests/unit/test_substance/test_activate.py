"""运动层测试 — activate 推理引擎核心链路 + decay / conflict / compress。

遵循 CLAUDE.md 测试原则：仅覆盖核心信任路径，不穷举边界变体。
"""

from datetime import datetime, timedelta

import pytest

from long_earn.substance.model import FilterLogic, Substance, SubstanceForm
from long_earn.substance.motion import activate, compress, decay, detect_conflicts
from long_earn.substance.store import SubstanceStore


def _sub(content: str, keys: list[str], **kw) -> Substance:
    return Substance(form=SubstanceForm.KNOWLEDGE, content=content, keys=keys, **kw)


# ── activate：关键词触发 ──────────────────────────────────────


def test_keyword_match_activates():
    """含 key 的物质被激活，不含的不激活。"""
    store = SubstanceStore()
    store.add(_sub("动量策略根据近期涨幅", keys=["动量", "涨幅"]))
    store.add(_sub("价值投资", keys=["价值"]))

    result = activate("动量选股", store, budget=10)
    assert len(result) == 1
    assert "动量" in result[0].content


def test_no_keys_not_activated():
    """没有 keys 的物质不会被激活。"""
    store = SubstanceStore()
    store.add(_sub("无关键词知识", keys=[]))
    assert activate("任何查询", store, budget=10) == []


# ── activate：filter_logic 四模式 ──────────────────────────────


@pytest.mark.parametrize(
    "logic,filter_keys,text,expected",
    [
        (FilterLogic.AND_ANY, ["A股", "港股"], "策略 A股市场", True),
        (FilterLogic.AND_ALL, ["大盘", "蓝筹"], "策略 大盘蓝筹股", True),
        (FilterLogic.NOT_ANY, ["退市", "ST"], "策略 退市风险", False),
        (FilterLogic.NOT_ALL, ["退市", "ST"], "策略 退市ST股票", False),
    ],
    ids=["and_any", "and_all", "not_any", "not_all"],
)
def test_filter_logic_modes(logic, filter_keys, text, expected):
    store = SubstanceStore()
    store.add(_sub("策略", keys=["策略"], filter_keys=filter_keys, filter_logic=logic))
    result = activate(text, store, budget=10)
    assert (len(result) == 1) == expected


# ── activate：递归激活 ────────────────────────────────────────


def test_recursive_activation_chain():
    """已激活物质内容再激活其他物质。"""
    store = SubstanceStore()
    store.add(_sub("动量策略根据涨幅", keys=["动量"]))
    store.add(_sub("涨幅因子计算", keys=["涨幅"]))

    result = activate("动量", store, budget=10, max_recursion=3)
    contents = [s.content for s in result]
    assert "动量策略根据涨幅" in contents
    assert "涨幅因子计算" in contents  # 递归激活


# ── activate：conflict_group 互斥 ─────────────────────────────


def test_conflict_group_keeps_highest_order():
    """同 conflict_group 取 insertion_order 最高者。"""
    store = SubstanceStore()
    store.add(_sub("旧观点", keys=["市场"], conflict_group="g", insertion_order=1))
    store.add(_sub("新观点", keys=["市场"], conflict_group="g", insertion_order=5))
    result = activate("市场", store, budget=10)
    assert len(result) == 1
    assert "新观点" in result[0].content


# ── activate：预算截断 + 排序 ─────────────────────────────────


def test_budget_truncation_and_order():
    """budget 限制返回数 + 按 insertion_order 降序。"""
    store = SubstanceStore()
    for i in range(5):
        store.add(_sub(f"策略{i}", keys=["策略"], insertion_order=i))

    result = activate("策略", store, budget=2)
    assert len(result) == 2
    assert result[0].insertion_order == 4
    assert result[1].insertion_order == 3


# ── activate：时间过滤 ────────────────────────────────────────


def test_time_filtering():
    """visible_from 未来不激活 + expires_at 过期不激活。"""
    now = datetime.now()
    store = SubstanceStore()

    store.add(
        Substance(
            form=SubstanceForm.EVENT,
            content="未来事件",
            keys=["事件"],
            visible_from=now + timedelta(days=1),
        )
    )
    store.add(
        Substance(
            form=SubstanceForm.EVENT,
            content="过期事件",
            keys=["事件"],
            expires_at=now - timedelta(days=1),
        )
    )
    assert activate("事件", store, budget=10) == []


# ── activate：空库 ────────────────────────────────────────────


def test_empty_store_returns_empty():
    store = SubstanceStore()
    assert activate("查询", store, budget=10) == []


# ── decay ─────────────────────────────────────────────────────


def test_decay_marks_old_substances():
    """衰减标记旧物质为 decayed。"""
    store = SubstanceStore()
    store.add(Substance(form=SubstanceForm.EVENT, content="新事件"))
    old = Substance(
        form=SubstanceForm.EVENT,
        content="旧事件",
        created_at=datetime.now() - timedelta(days=30),
    )
    store.add(old)

    assert decay(store) >= 1
    assert old.metadata.get("decayed") is True


# ── detect_conflicts ──────────────────────────────────────────


def test_detect_conflicts_contradictory():
    """矛盾观点被检测为冲突，不相关内容不触发。"""
    store = SubstanceStore()
    store.add(
        Substance(
            form=SubstanceForm.KNOWLEDGE,
            content="this strategy works very well 利好买入",
        )
    )
    contradictory = Substance(
        form=SubstanceForm.KNOWLEDGE, content="this strategy works very bad 利空卖出"
    )
    unrelated = Substance(form=SubstanceForm.KNOWLEDGE, content="Python 编程教程")

    assert len(detect_conflicts(store, contradictory, min_similarity=0.3)) >= 1
    assert len(detect_conflicts(store, unrelated, min_similarity=0.5)) == 0


# ── compress ──────────────────────────────────────────────────


def test_compress_merges_similar():
    """高相似物质被合并，不相似的不合并。"""
    store = SubstanceStore()
    store.add(_sub("momentum strategy 动量策略根据近期涨幅", keys=[]))
    store.add(_sub("momentum strategy 动量策略根据过去涨幅", keys=[]))
    store.add(_sub("mean reversion 均值回归策略", keys=[]))

    removed = compress(store, min_similarity=0.3)
    assert removed > 0
    assert store.count < 3

    store2 = SubstanceStore()
    store2.add(_sub("动量策略选股", keys=[]))
    store2.add(_sub("Python 编程教程", keys=[]))
    assert compress(store2, min_similarity=0.9) == 0
