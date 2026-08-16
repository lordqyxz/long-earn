"""审计链接口契约测试：response_model 稳定生成、响应符合新 schema。

验证目标（防类型漂移回归）：
1. GET /api/runs/{run_id}/audit/{trace_id} 响应符合 AuditEventsResponse 契约
   （openapi 生成 AuditEventItem/AuditChainEvent 命名 schema，前端 api:gen 不再删类型）；
2. BacktestAnalyzer 真实归因链逻辑（classmethod，不触 PG）的产物能被
   TradeAttribution / AuditChainEvent 校验，避免 response_model 引入运行时 500。

单测全程 Mock，不读写共享 PostgreSQL。
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from long_earn.app.analyzer import BacktestAnalyzer
from long_earn.app.app import _create_app
from long_earn.app.schemas.audit import (
    AuditChainEvent,
    AuditEventsResponse,
    TradeAttribution,
)

# 与 export_audit_event 返回结构一致的样例（含 timestamp 为 None 的历史数据形态）
_SAMPLE_EVENTS: list[dict[str, Any]] = [
    {
        "event_type": "FILL",
        "component": "Broker",
        "status": "SUCCESS",
        "timestamp": "2023-01-03 09:32:00",
        "payload": {"symbol": "A", "type": "BUY", "price": 10.0, "quantity": 500.0},
    },
    {
        "event_type": "SIGNAL",
        "component": "Strategy",
        "status": "SUCCESS",
        "timestamp": None,
        "payload": {"signals": {"A": 0.5}, "strategy_id": "test-mom"},
    },
]


def _build_app_with_mock_analyzer(
    monkeypatch: pytest.MonkeyPatch, analyzer: MagicMock
) -> FastAPI:
    """以 Mock 分析器构建应用（不触碰 PostgreSQL）。"""
    monkeypatch.setattr("long_earn.app.app.BacktestAnalyzer", lambda: analyzer)
    monkeypatch.setattr("long_earn.app.app.EventAnalyzer", MagicMock)
    return _create_app()


def test_audit_endpoint_response_conforms_to_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """audit 接口响应可被 AuditEventsResponse 校验（response_model 契约一致）。"""
    analyzer = MagicMock()
    analyzer.export_audit_event.return_value = _SAMPLE_EVENTS
    client = TestClient(_build_app_with_mock_analyzer(monkeypatch, analyzer))

    resp = client.get("/api/runs/run-1/audit/trace-1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "run-1"
    assert body["trace_id"] == "trace-1"
    parsed = AuditEventsResponse.model_validate(body)
    assert len(parsed.events) == 2
    assert parsed.events[0].event_type == "FILL"
    assert parsed.events[0].payload["symbol"] == "A"
    # timestamp 允许为 null（历史数据形态）
    assert parsed.events[1].timestamp is None


def test_audit_endpoint_404_when_no_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """无匹配事件时接口返回 404（保持既有行为）。"""
    analyzer = MagicMock()
    analyzer.export_audit_event.return_value = []
    client = TestClient(_build_app_with_mock_analyzer(monkeypatch, analyzer))

    resp = client.get("/api/runs/run-1/audit/no-such-trace")

    assert resp.status_code == 404


def test_openapi_emits_audit_named_schemas(monkeypatch: pytest.MonkeyPatch) -> None:
    """openapi 必须生成 AuditEventItem / AuditChainEvent 等命名 schema 且 audit 端点引用之。

    这是前端 api:gen 的类型来源——缺失会删掉 types.gen.ts 里的对应类型，导致
    BacktestDetail.tsx 编译断裂（本任务根治的类型漂移）。
    """
    analyzer = MagicMock()
    app = _build_app_with_mock_analyzer(monkeypatch, analyzer)
    openapi = app.openapi()
    schemas = openapi["components"]["schemas"]

    assert "AuditEventItem" in schemas
    assert "AuditChainEvent" in schemas
    assert "AuditEventsResponse" in schemas
    assert "TradeAttribution" in schemas

    op = openapi["paths"]["/api/runs/{run_id}/audit/{trace_id}"]["get"]
    schema_ref = op["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema_ref["$ref"].endswith("/AuditEventsResponse")


def _make_by_trace() -> dict[str, dict[str, Any]]:
    """构造与 export_trade_attribution 中间结构一致的 by_trace（信号单链）。"""
    return {
        "sig-1": {
            "parent": "",
            "event_type": "SIGNAL",
            "payload": {
                "signals": {"A": 0.5},
                "strategy_id": "test-mom",
                "risk_triggered": False,
            },
            "component": "Strategy",
            "status": "SUCCESS",
            "timestamp": "2023-01-03 09:30:00",
        },
        "ord-1": {
            "parent": "sig-1",
            "event_type": "ORDER",
            "payload": {"symbol": "A", "type": "BUY", "quantity": 500.0},
            "component": "Portfolio",
            "status": "SUCCESS",
            "timestamp": "2023-01-03 09:31:00",
        },
        "fill-1": {
            "parent": "ord-1",
            "event_type": "FILL",
            "payload": {
                "symbol": "A",
                "type": "BUY",
                "price": 10.0,
                "quantity": 500.0,
            },
            "component": "Broker",
            "status": "SUCCESS",
            "timestamp": "2023-01-03 09:32:00",
        },
    }


def test_analyzer_attribution_chain_validates_against_schema() -> None:
    """真实 analyzer 归因链产物可被 TradeAttribution / AuditChainEvent 校验。

    响应模型不改变运行时行为：归因链结构（含 chain.events 紧凑摘要）必须与
    新 schema 完全兼容，否则 /trades 会因 response_model 校验失败而 500。
    """
    by_trace = _make_by_trace()
    att = BacktestAnalyzer._resolve_trade_attribution("fill-1", "ord-1", by_trace)

    parsed = TradeAttribution.model_validate(att)
    assert parsed.kind == "signal"
    assert parsed.chain is not None
    assert parsed.chain.events is not None
    # 链上三节点齐全且每个节点都是合法 AuditChainEvent
    for key in ("upstream", "order", "fill"):
        node = getattr(parsed.chain.events, key)
        assert isinstance(node, AuditChainEvent)
        assert node.event_type
        assert node.summary
    assert parsed.chain.events.upstream is not None
    assert "策略" in parsed.chain.events.upstream.summary
