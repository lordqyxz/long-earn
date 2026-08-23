"""算子执行器决策依据（rationale）测试。

rationale 是把"为什么选出这些标的"落到审计的关键：每只选中标的的
因子值 + 排名、候选/选中数、以及算子步骤的人类可读公式描述。
"""

from datetime import datetime, timedelta

import polars as pl

from long_earn.backtest.engine.operator_executor import (
    OperatorStrategyExecutor,
    resolve_factor_step,
    resolve_signal_step,
)


def _make_panel() -> pl.DataFrame:
    """6 天 × 4 只，增长率逐股递减（A>B>C>D），returns(2) 可区分排名。"""
    symbols = ["A", "B", "C", "D"]
    growth = {"A": 1.02, "B": 1.015, "C": 1.01, "D": 1.005}
    rows = []
    for i in range(6):
        for sym in symbols:
            price = 10.0 * (growth[sym] ** i)
            ts = datetime(2024, 1, 1) + timedelta(days=i)
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": sym,
                    "open": price * 0.99,
                    "high": price * 1.01,
                    "low": price * 0.98,
                    "close": price,
                    "volume": 10000,
                }
            )
    return pl.DataFrame(rows)


def _executor() -> OperatorStrategyExecutor:
    return OperatorStrategyExecutor(
        [
            resolve_factor_step(
                {
                    "op": "returns",
                    "alias": "mom",
                    "params": {"field": "close", "period": 2},
                }
            )
        ],
        [
            resolve_signal_step(
                {
                    "op": "rank_top",
                    "params": {"field": "mom", "top": 2, "ascending": False},
                }
            )
        ],
    )


def test_execute_with_rationale_returns_factor_values_and_rank():
    """rationale.selection 应带每只选中标的的因子值 + 排名，universe/selected 数正确。"""
    panel = _make_panel()
    ts = datetime(2024, 1, 6)
    selected, rationale = _executor().execute_with_rationale(panel, ts)

    assert selected == ["A", "B"]
    sel = rationale["selection"]
    assert [s["symbol"] for s in sel] == ["A", "B"]
    assert [s["rank"] for s in sel] == [1, 2]
    # 每只都带因子值 mom（浮点）
    assert all("mom" in s and isinstance(s["mom"], float) for s in sel)
    # A 动量最高（第 1 名）
    assert sel[0]["mom"] > sel[1]["mom"]
    assert rationale["universe_size"] == 4
    assert rationale["selected_count"] == 2


def test_execute_with_rationale_criteria_describes_formula():
    """criteria 应包含因子与信号步骤的人类可读描述，returns 标记为百分比。"""
    _, rationale = _executor().execute_with_rationale(
        _make_panel(), datetime(2024, 1, 6)
    )

    criteria = rationale["criteria"]
    assert [c["op"] for c in criteria] == ["returns", "rank_top"]
    factor_step = criteria[0]
    assert factor_step["step"] == "factor"
    assert factor_step["alias"] == "mom"
    assert factor_step["format"] == "pct"
    assert "2 期收益率" in factor_step["desc"]
    rank_step = criteria[1]
    assert rank_step["step"] == "signal"
    assert "前 2" in rank_step["desc"]


def test_criteria_step_has_kind_and_segments():
    """criteria 每步应下发 kind + segments 结构化渲染数据（接口传数据，前端动态渲染）。"""
    _, rationale = _executor().execute_with_rationale(
        _make_panel(), datetime(2024, 1, 6)
    )
    criteria = rationale["criteria"]

    factor_step = criteria[0]
    assert factor_step["kind"] == "factor"
    # returns 模板段：字段高亮 + 期数标量 + 文本
    assert factor_step["segments"] == [
        {"type": "field", "value": "close"},
        {"type": "text", "value": " 的 "},
        {"type": "value", "value": 2},
        {"type": "text", "value": " 期收益率"},
    ]

    rank_step = criteria[1]
    assert rank_step["kind"] == "rank"
    assert rank_step["segments"][0] == {"type": "text", "value": "按 "}
    assert rank_step["segments"][1] == {"type": "field", "value": "mom"}
    assert rank_step["segments"][-1] == {"type": "value", "value": 2}


def test_criteria_filter_step_segments():
    """filter_threshold 应下发 filter kind 与「字段 符号 阈值」渲染段。"""
    executor = OperatorStrategyExecutor(
        [],
        [
            resolve_signal_step(
                {
                    "op": "filter_threshold",
                    "params": {"field": "close", "op": ">", "value": 9.5},
                }
            )
        ],
    )
    _, rationale = executor.execute_with_rationale(_make_panel(), datetime(2024, 1, 6))
    step = rationale["criteria"][0]
    assert step["kind"] == "filter"
    assert step["segments"] == [
        {"type": "text", "value": "筛选 "},
        {"type": "field", "value": "close"},
        {"type": "symbol", "value": ">"},
        {"type": "value", "value": 9.5},
    ]


def test_criteria_generic_segments_for_unknown_operator():
    """无专属模板的算子（technical 如 macd）也应下发通用段：列名参数高亮为 field 段。"""
    executor = OperatorStrategyExecutor(
        [
            resolve_factor_step(
                {
                    "op": "macd",
                    "alias": "m",
                    "params": {"field": "close", "fast": 12, "slow": 26, "signal": 9},
                }
            )
        ],
        [],
    )
    _, rationale = executor.execute_with_rationale(_make_panel(), datetime(2024, 1, 6))
    step = rationale["criteria"][0]
    assert step["kind"] == "factor"  # technical 归 factor 样式
    types = [s["type"] for s in step["segments"]]
    assert types == [
        "text",
        "field",
        "text",
        "value",
        "text",
        "value",
        "text",
        "value",
        "text",
    ]
    assert step["segments"][1] == {
        "type": "field",
        "value": "close",
    }  # field_params 列名高亮
    assert step["segments"][0]["value"] == "macd("
    assert step["segments"][-1]["value"] == ")"


def test_execute_returns_symbols_only_keeps_compat():
    """旧接口 execute 仍只返回 symbol 列表（不破坏既有调用方）。"""
    selected = _executor().execute(_make_panel(), datetime(2024, 1, 6))
    assert selected == ["A", "B"]


def test_execute_with_rationale_empty_selection_is_graceful():
    """信号过滤后无选中时，返回 (空列表, 空 selection 的 rationale)，不抛错。"""
    # 用只含 filter（无 rank）的 executor，且阈值排除所有标的
    executor = OperatorStrategyExecutor(
        [],
        [
            resolve_signal_step(
                {
                    "op": "filter_threshold",
                    "params": {"field": "close", "op": ">", "value": 1e9},
                }
            )
        ],
    )
    panel = _make_panel()
    selected, rationale = executor.execute_with_rationale(panel, datetime(2024, 1, 6))
    assert selected == []
    assert rationale["selection"] == []
    assert rationale["selected_count"] == 0
