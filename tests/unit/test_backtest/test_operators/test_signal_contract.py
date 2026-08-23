"""信号算子截面性（cross-sectional）契约测试。

验证目标（I3 守卫，防预计算路径静默算错）：
1. ``resolve_signal_step`` 只接受截面算子——预计算模式下 signal 算子
   跑在单日截面上，非截面算子（依赖历史的 shift/sma 等）会静默产出
   null/错值而不报错；
2. 目录中所有 filter / rank 类算子必须声明 ``cross_sectional = True``
   （参数化面向接口，新增算子忘声明即测试失败）。

prove_causality 只证明时序因果（不窥未来），不证明截面性——两者正交。
"""

from __future__ import annotations

import pytest

from long_earn.backtest.engine.operator_executor import resolve_signal_step
from long_earn.backtest.operators._loader import OPERATOR_REGISTRY
from long_earn.backtest.operators.base import VALID_CATEGORIES


def test_cross_sectional_signal_accepted() -> None:
    """截面算子（rank_top / filter_threshold）可作信号步骤。"""
    resolve_signal_step(
        {"type": "operator", "op": "rank_top", "params": {"field": "f", "top": 3}}
    )
    resolve_signal_step(
        {
            "type": "operator",
            "op": "filter_threshold",
            "params": {"field": "f", "op": ">", "value": 0},
        }
    )


def test_non_cross_sectional_signal_rejected() -> None:
    """非截面算子（时序 sma）作信号步骤在解析期被拒绝。"""
    with pytest.raises(ValueError, match="截面"):
        resolve_signal_step(
            {
                "type": "operator",
                "op": "sma",
                "params": {"field": "close", "window": 5},
            }
        )


def test_filter_rank_ops_are_cross_sectional() -> None:
    """所有 filter / rank 类算子必须声明 cross_sectional=True。

    信号步骤语义上属于这两类；预计算路径要求信号算子在单日截面上的
    输出与全历史面板一致。新增非截面 filter/rank 算子需显式声明 False
    并接受 resolve_signal_step 拒绝（届时更新本测试预期）。
    """
    assert OPERATOR_REGISTRY, "测试前提：算子目录非空"
    for name, op in sorted(OPERATOR_REGISTRY.items()):
        op_cls = type(op)
        if op_cls.category in {"filter", "rank"}:
            assert op_cls.cross_sectional is True, (
                f"{name}（category={op_cls.category}）未声明 "
                "cross_sectional=True，信号算子截面性契约缺失"
            )


def test_valid_categories_unchanged() -> None:
    """目录 category 集合守卫（新增类别时同步审视信号截面性契约）。"""
    assert (
        frozenset({"factor", "filter", "rank", "compose", "technical"})
        == VALID_CATEGORIES
    )
