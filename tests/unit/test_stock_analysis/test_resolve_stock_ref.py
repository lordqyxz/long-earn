"""resolve_stock_ref 标的解析节点契约（ADR-021：确定性先行、LLM 兜底）。"""

from __future__ import annotations

from unittest.mock import MagicMock

from long_earn.stock_analysis.subgraph import resolve_stock_ref


def _make_context(stock_code_by_name: str = "") -> MagicMock:
    ctx = MagicMock()
    ctx.logger = MagicMock()
    ctx.require_stock.return_value.get_stock_code_by_name.return_value = (
        stock_code_by_name
    )
    return ctx


def test_code_in_query_resolves_without_llm() -> None:
    """查询文本含 6 位代码（含交易所前后缀）时正则直判，不消耗 LLM"""
    ctx = _make_context()

    result = resolve_stock_ref(
        {"query": "帮我分析贵州茅台600519近期走势", "stock_code": "", "stock_name": ""},
        ctx,
    )

    assert result["stock_code"] == "600519"
    ctx.require_llm.assert_not_called()

    result = resolve_stock_ref({"query": "000001.SZ 近期如何"}, ctx)

    assert result["stock_code"] == "000001"
    ctx.require_llm.assert_not_called()


def test_existing_code_short_circuits() -> None:
    """状态已有 stock_code 时直接使用"""
    ctx = _make_context()

    result = resolve_stock_ref(
        {"query": "分析茅台", "stock_code": "600519", "stock_name": ""},
        ctx,
    )

    assert result["stock_code"] == "600519"
    ctx.require_llm.assert_not_called()


def test_name_lookup_is_deterministic_first() -> None:
    """已知名称走板块字典查找（确定性），不走 LLM"""
    ctx = _make_context(stock_code_by_name="600519")

    result = resolve_stock_ref(
        {"query": "分析茅台", "stock_code": "", "stock_name": "贵州茅台"},
        ctx,
    )

    assert result["stock_code"] == "600519"
    ctx.require_llm.assert_not_called()


def test_llm_fallback_when_deterministic_misses() -> None:
    """确定性手段全部未命中才走 LLM 兜底"""
    ctx = _make_context()
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(
        content='{"stock_name": "宁德时代", "stock_code": "300750"}'
    )
    ctx.require_llm.return_value = llm

    result = resolve_stock_ref(
        {"query": "分析电池龙头", "stock_code": "", "stock_name": ""},
        ctx,
    )

    assert result["stock_code"] == "300750"
    assert result["stock_name"] == "宁德时代"
    llm.invoke.assert_called_once()


def test_llm_fallback_json_error_degrades_gracefully() -> None:
    """LLM 返回非法 JSON 时空解析结果，不抛异常"""
    ctx = _make_context()
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="不是 JSON")
    ctx.require_llm.return_value = llm

    result = resolve_stock_ref(
        {"query": "分析某龙头", "stock_code": "", "stock_name": ""},
        ctx,
    )

    assert result["stock_code"] == ""
    assert result["stock_name"] == ""
