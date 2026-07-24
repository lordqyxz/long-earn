"""HTR 接入 AcceptanceGate 训练集门测试（ADR-009 收尾）。

验证 _executor_node / _executor_single_node 在优化版 sharpe 严格提升时接受、
sharpe 退化时拒绝（候选标记 rejected，不更新 evidence）。
"""

from __future__ import annotations

from typing import Any

import pytest

from long_earn.strategy_optimization.acceptance import AcceptanceGate
from long_earn.strategy_rd.htr_subgraph import (
    _executor_node,
    _executor_single_node,
)
from long_earn.strategy_rd.hypothesis_tree import HypothesisTree


class _FakeResearchAgent:
    """optimize_strategy 直接回传预设 strategy 字典。"""

    def __init__(self, optimized: dict[str, Any]) -> None:
        self._optimized = optimized

    def optimize_strategy(self, *, strategy, improvement_suggestions, previous_backtest):
        return self._optimized


class _FakeDevelopAgent:
    """develop_strategy 回传固定 YAML 字符串。"""

    def __init__(self, yaml_str: str) -> None:
        self._yaml = yaml_str

    def develop_strategy(self, strategy):
        return self._yaml


class _FakeBacktestService:
    """run() 回传固定 result 字典。"""

    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def run(self, *, strategy_yaml, start_date="", end_date=""):
        return self._result


class _FakeLogger:
    """简易 logger，记录 warning/error/info 调用。"""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.infos: list[str] = []

    def info(self, msg: str) -> None:
        self.infos.append(msg)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)


def _make_tree_data() -> dict[str, Any]:
    """用 HypothesisTree API 构造一棵单节点树并序列化为 dict。"""
    tree = HypothesisTree(run_id="test_run")
    tree.init_root(hypothesis="测试假设", direction="", strategy_ref="")
    # 把根节点 id 改为 n1 以便测试引用（保持 root_id 不变即可，这里用 root）
    return tree.serialize()


def _bt(sharpe: float, ret: float = 0.1) -> dict[str, Any]:
    """构造一个非退化的回测结果字典。"""
    return {
        "sharpe_ratio": sharpe,
        "total_return": ret,
        "strategy_diagnostics": {"degenerate": False},
    }


def test_executor_node_rejects_sharpe_regression() -> None:
    """优化版训练集 sharpe 下降时，AcceptanceGate 拒绝，候选不进 results 的 dev_score。"""
    state: dict[str, Any] = {
        "hypothesis_tree": _make_tree_data(),
        "selected_leaves": ["root"],
        "strategy": {"name": "base"},
        "backtest_result": _bt(1.5),  # baseline sharpe 1.5
    }
    # 优化版 sharpe 0.8 < baseline 1.5 → AcceptanceGate 拒绝
    result = _executor_node(
        state,  # type: ignore[arg-type]
        research_agent=_FakeResearchAgent({"name": "optimized"}),
        develop_agent=_FakeDevelopAgent("strategy: name: opt"),
        backtest_service=_FakeBacktestService(_bt(0.8)),
        logger=_FakeLogger(),
        gate=AcceptanceGate(),
    )
    assert result["executor_results"][0]["rejected"] is True
    assert "dev_score" not in result["executor_results"][0]
    assert "rejection_reason" in result["executor_results"][0]


def test_executor_node_accepts_sharpe_improvement() -> None:
    """优化版训练集 sharpe 严格提升时，AcceptanceGate 接受，候选写入 dev_score。"""
    state: dict[str, Any] = {
        "hypothesis_tree": _make_tree_data(),
        "selected_leaves": ["root"],
        "strategy": {"name": "base"},
        "backtest_result": _bt(1.0),  # baseline sharpe 1.0
    }
    # 优化版 sharpe 1.5 > baseline 1.0 + eps → AcceptanceGate 接受
    result = _executor_node(
        state,  # type: ignore[arg-type]
        research_agent=_FakeResearchAgent({"name": "optimized"}),
        develop_agent=_FakeDevelopAgent("strategy: name: opt"),
        backtest_service=_FakeBacktestService(_bt(1.5)),
        logger=_FakeLogger(),
        gate=AcceptanceGate(),
    )
    assert result["executor_results"][0]["dev_score"] == pytest.approx(1.5)
    assert result["executor_results"][0].get("rejected") is not True


def test_executor_single_node_rejects_sharpe_regression() -> None:
    """并行模式同样接入 AcceptanceGate — sharpe 退化时拒绝。"""
    state: dict[str, Any] = {
        "hypothesis_tree": _make_tree_data(),
        "strategy": {"name": "base"},
        "backtest_result": _bt(2.0),  # baseline sharpe 2.0
    }
    result = _executor_single_node(
        state,  # type: ignore[arg-type]
        node_id="root",
        research_agent=_FakeResearchAgent({"name": "optimized"}),
        develop_agent=_FakeDevelopAgent("strategy: name: opt"),
        backtest_service=_FakeBacktestService(_bt(0.5)),  # 优化版 0.5 < 2.0
        logger=_FakeLogger(),
        gate=AcceptanceGate(),
    )
    assert result["executor_results"][0]["rejected"] is True
    assert "dev_score" not in result["executor_results"][0]


def test_executor_node_skip_gate_when_none() -> None:
    """gate=None 时跳过校验，保持向后兼容（既有测试无 gate 注入）。"""
    state: dict[str, Any] = {
        "hypothesis_tree": _make_tree_data(),
        "selected_leaves": ["root"],
        "strategy": {"name": "base"},
        "backtest_result": _bt(1.0),
    }
    # 优化版 sharpe 0.5 < 1.0，但 gate=None → 不校验，直接接受
    result = _executor_node(
        state,  # type: ignore[arg-type]
        research_agent=_FakeResearchAgent({"name": "optimized"}),
        develop_agent=_FakeDevelopAgent("strategy: name: opt"),
        backtest_service=_FakeBacktestService(_bt(0.5)),
        logger=_FakeLogger(),
        gate=None,
    )
    assert result["executor_results"][0]["dev_score"] == pytest.approx(0.5)
    assert "rejected" not in result["executor_results"][0]
