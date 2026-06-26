"""activate 推理引擎正确性测试 — WorldInfo 激活机制全链路验证。

覆盖：
- 关键词触发（精确匹配 + 正则匹配）
- filter_logic 四种模式（AND_ANY / AND_ALL / NOT_ANY / NOT_ALL）
- 递归激活（已激活物质内容再激活其他物质）
- conflict_group 互斥（同组取 insertion_order 最高者）
- 预算截断（budget 限制返回数）
- 时间过滤（visible_from / expires_at）
- insertion_order 排序
- 空库 / 无 keys 物质不激活
"""

from datetime import datetime, timedelta

from long_earn.substance.model import FilterLogic, Substance, SubstanceForm
from long_earn.substance.motion import activate
from long_earn.substance.store import SubstanceStore


class TestKeywordActivation:
    """关键词触发机制"""

    def test_exact_keyword_match(self):
        """文本包含物质 key 时被激活"""
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="动量策略根据近期涨幅选股",
                keys=["动量", "涨幅"],
            )
        )
        result = activate("动量选股", store, budget=10)
        assert len(result) == 1
        assert "动量" in result[0].content

    def test_no_keyword_match(self):
        """文本不包含任何 key 时不激活"""
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="动量策略",
                keys=["动量"],
            )
        )
        result = activate("价值投资", store, budget=10)
        assert len(result) == 0

    def test_multiple_substances_activated(self):
        """多个物质同时命中关键词"""
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="动量策略",
                keys=["动量"],
            )
        )
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="动量因子分析",
                keys=["动量"],
            )
        )
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="价值投资",
                keys=["价值"],
            )
        )
        result = activate("动量", store, budget=10)
        assert len(result) == 2
        contents = [s.content for s in result]
        assert "动量策略" in contents
        assert "动量因子分析" in contents

    def test_substance_without_keys_not_activated(self):
        """没有 keys 的物质不会被激活"""
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="无关键词的知识",
            )
        )
        result = activate("关键词", store, budget=10)
        assert len(result) == 0


class TestFilterLogic:
    """filter_keys 四种过滤逻辑"""

    def test_and_any_passes_when_any_filter_key_present(self):
        """AND_ANY: 文本含任一 filter_key 则通过"""
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="A股策略",
                keys=["策略"],
                filter_keys=["A股", "港股"],
                filter_logic=FilterLogic.AND_ANY,
            )
        )
        result = activate("策略 A股市场", store, budget=10)
        assert len(result) == 1

    def test_and_any_blocks_when_no_filter_key_present(self):
        """AND_ANY: 文本不含任何 filter_key 则不激活"""
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="A股策略",
                keys=["策略"],
                filter_keys=["A股", "港股"],
                filter_logic=FilterLogic.AND_ANY,
            )
        )
        result = activate("策略 美股市场", store, budget=10)
        assert len(result) == 0

    def test_and_all_passes_when_all_filter_keys_present(self):
        """AND_ALL: 文本含全部 filter_key 则通过"""
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="大盘蓝筹策略",
                keys=["策略"],
                filter_keys=["大盘", "蓝筹"],
                filter_logic=FilterLogic.AND_ALL,
            )
        )
        result = activate("策略 大盘蓝筹股", store, budget=10)
        assert len(result) == 1

    def test_and_all_blocks_when_partial_match(self):
        """AND_ALL: 文本只含部分 filter_key 则不激活"""
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="大盘蓝筹策略",
                keys=["策略"],
                filter_keys=["大盘", "蓝筹"],
                filter_logic=FilterLogic.AND_ALL,
            )
        )
        result = activate("策略 大盘股", store, budget=10)
        assert len(result) == 0

    def test_not_any_blocks_when_any_filter_key_present(self):
        """NOT_ANY: 文本含任一 filter_key 则不激活（排除逻辑）"""
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="普通策略",
                keys=["策略"],
                filter_keys=["退市", "ST"],
                filter_logic=FilterLogic.NOT_ANY,
            )
        )
        result = activate("策略 退市风险", store, budget=10)
        assert len(result) == 0

    def test_not_any_passes_when_no_filter_key_present(self):
        """NOT_ANY: 文本不含任何 filter_key 则通过"""
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="普通策略",
                keys=["策略"],
                filter_keys=["退市", "ST"],
                filter_logic=FilterLogic.NOT_ANY,
            )
        )
        result = activate("策略 正常股票", store, budget=10)
        assert len(result) == 1

    def test_not_all_blocks_when_all_filter_keys_present(self):
        """NOT_ALL: 文本含全部 filter_key 则不激活"""
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="投资策略",
                keys=["策略"],
                filter_keys=["退市", "ST"],
                filter_logic=FilterLogic.NOT_ALL,
            )
        )
        result = activate("策略 退市ST股票", store, budget=10)
        assert len(result) == 0

    def test_not_all_passes_when_partial_match(self):
        """NOT_ALL: 文本只含部分 filter_key 则通过"""
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="投资策略",
                keys=["策略"],
                filter_keys=["退市", "ST"],
                filter_logic=FilterLogic.NOT_ALL,
            )
        )
        result = activate("策略 退市风险", store, budget=10)
        assert len(result) == 1


class TestRecursiveActivation:
    """递归激活机制"""

    def test_recursive_activation_chain(self):
        """已激活物质的内容再激活其他物质"""
        store = SubstanceStore()
        # A 的 key 是 "动量"，会因查询 "动量" 被激活
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="动量策略根据近期涨幅选股",
                keys=["动量"],
            )
        )
        # B 的 key 是 "涨幅"，A 的内容包含 "涨幅"，所以 B 会被递归激活
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="涨幅因子计算方法",
                keys=["涨幅"],
            )
        )
        # C 的 key 是 "不存在"，不会被激活
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="不存在的东西",
                keys=["不存在"],
            )
        )
        result = activate("动量", store, budget=10, max_recursion=3)
        contents = [s.content for s in result]
        assert "动量策略根据近期涨幅选股" in contents
        assert "涨幅因子计算方法" in contents  # 递归激活
        assert "不存在的东西" not in contents

    def test_max_recursion_zero(self):
        """max_recursion=0 时只做第一轮，不递归"""
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="动量策略根据涨幅",
                keys=["动量"],
            )
        )
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="涨幅因子",
                keys=["涨幅"],
            )
        )
        result = activate("动量", store, budget=10, max_recursion=0)
        contents = [s.content for s in result]
        assert "动量策略根据涨幅" in contents
        assert "涨幅因子" not in contents  # 不递归


class TestConflictGroupResolution:
    """conflict_group 互斥机制"""

    def test_conflict_group_keeps_highest_order(self):
        """同 conflict_group 取 insertion_order 最高者"""
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="旧观点: 看多",
                keys=["市场"],
                conflict_group="market_view",
                insertion_order=1,
            )
        )
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="新观点: 看空",
                keys=["市场"],
                conflict_group="market_view",
                insertion_order=5,
            )
        )
        result = activate("市场", store, budget=10)
        assert len(result) == 1
        assert "新观点" in result[0].content

    def test_no_conflict_group_all_activated(self):
        """无 conflict_group 的物质不受互斥影响"""
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="观点A",
                keys=["市场"],
            )
        )
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="观点B",
                keys=["市场"],
            )
        )
        result = activate("市场", store, budget=10)
        assert len(result) == 2


class TestBudgetTruncation:
    """预算截断机制"""

    def test_budget_limits_result_count(self):
        """budget 限制返回数量"""
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
        result = activate("策略", store, budget=3)
        assert len(result) == 3

    def test_budget_returns_highest_insertion_order(self):
        """预算截断保留 insertion_order 最高的物质"""
        store = SubstanceStore()
        for i in range(5):
            store.add(
                Substance(
                    form=SubstanceForm.KNOWLEDGE,
                    content=f"策略{i}",
                    keys=["策略"],
                    insertion_order=i,
                )
            )
        result = activate("策略", store, budget=2)
        assert len(result) == 2
        # insertion_order 降序：4, 3
        assert result[0].insertion_order == 4
        assert result[1].insertion_order == 3

    def test_budget_larger_than_results(self):
        """budget 大于候选数时返回全部"""
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="策略",
                keys=["策略"],
            )
        )
        result = activate("策略", store, budget=100)
        assert len(result) == 1


class TestTimeFiltering:
    """时间过滤（visible_from / expires_at）"""

    def test_visible_from_blocks_future_substance(self):
        """visible_from 在未来的物质不被激活"""
        store = SubstanceStore()
        future = datetime.now() + timedelta(days=1)
        store.add(
            Substance(
                form=SubstanceForm.EVENT,
                content="未来事件",
                keys=["事件"],
                visible_from=future,
            )
        )
        result = activate("事件", store, budget=10)
        assert len(result) == 0

    def test_visible_from_allows_after_visible_time(self):
        """visible_from 已到期的物质正常激活"""
        past = datetime.now() - timedelta(days=1)
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.EVENT,
                content="已公开事件",
                keys=["事件"],
                visible_from=past,
            )
        )
        result = activate("事件", store, budget=10)
        assert len(result) == 1

    def test_expired_substance_not_activated(self):
        """已过期物质不被激活"""
        store = SubstanceStore()
        past = datetime.now() - timedelta(days=1)
        store.add(
            Substance(
                form=SubstanceForm.EVENT,
                content="过期事件",
                keys=["事件"],
                expires_at=past,
            )
        )
        result = activate("事件", store, budget=10)
        assert len(result) == 0

    def test_visible_at_parameter(self):
        """visible_at 参数控制时间过滤"""
        store = SubstanceStore()
        visible_time = datetime(2026, 6, 15)
        store.add(
            Substance(
                form=SubstanceForm.EVENT,
                content="6月事件",
                keys=["事件"],
                visible_from=visible_time,
            )
        )
        # 在 visible_from 之前查询
        result_before = activate(
            "事件", store, budget=10, visible_at=datetime(2026, 6, 10)
        )
        assert len(result_before) == 0

        # 在 visible_from 之后查询
        result_after = activate(
            "事件", store, budget=10, visible_at=datetime(2026, 6, 20)
        )
        assert len(result_after) == 1


class TestInsertionOrderSorting:
    """结果按 insertion_order 降序排列"""

    def test_results_sorted_by_insertion_order_desc(self):
        """激活结果按 insertion_order 降序"""
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="低优先级",
                keys=["策略"],
                insertion_order=1,
            )
        )
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="高优先级",
                keys=["策略"],
                insertion_order=10,
            )
        )
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="中优先级",
                keys=["策略"],
                insertion_order=5,
            )
        )
        result = activate("策略", store, budget=10)
        assert len(result) == 3
        assert result[0].insertion_order == 10
        assert result[1].insertion_order == 5
        assert result[2].insertion_order == 1


class TestEdgeCases:
    """边界条件"""

    def test_empty_store(self):
        """空库返回空列表"""
        store = SubstanceStore()
        result = activate("任何查询", store, budget=10)
        assert result == []

    def test_empty_query(self):
        """空查询不激活任何物质"""
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.KNOWLEDGE,
                content="策略",
                keys=["策略"],
            )
        )
        result = activate("", store, budget=10)
        assert len(result) == 0

    def test_different_forms_activated(self):
        """不同形态的物质都能被激活"""
        store = SubstanceStore()
        store.add(
            Substance(
                form=SubstanceForm.EVENT,
                content="市场事件",
                keys=["市场"],
            )
        )
        store.add(
            Substance(
                form=SubstanceForm.STRATEGY,
                content="市场策略",
                keys=["市场"],
            )
        )
        store.add(
            Substance(
                form=SubstanceForm.BACKTEST,
                content="市场回测",
                keys=["市场"],
            )
        )
        result = activate("市场", store, budget=10)
        assert len(result) == 3
        forms = {s.form for s in result}
        assert SubstanceForm.EVENT in forms
        assert SubstanceForm.STRATEGY in forms
        assert SubstanceForm.BACKTEST in forms
