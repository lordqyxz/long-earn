"""IncrementalSyncService 的接口契约测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from long_earn.services.incremental_sync import IncrementalSyncService


def test_sync_delegates_incremental_request_and_returns_stable_report() -> None:
    """同步模块将默认增量请求交给 miniQMT 采集实现并规范化结果。"""
    result = {
        "status": "ok",
        "mode": "smart",
        "universe": "csi300",
        "price_symbols": 300,
        "financial_symbols": 300,
        "cache_path": "test.duckdb",
    }
    ingestion = MagicMock()
    ingestion.run.return_value = result

    with patch(
        "long_earn.services.incremental_sync.DataIngestionService",
        return_value=ingestion,
    ):
        service = IncrementalSyncService()
        report = service.sync(universe="csi300", end_date="2026-08-13")

    ingestion.run.assert_called_once_with(
        universe="csi300",
        start_date="",
        end_date="2026-08-13",
        skip_financial=False,
        batch_size=0,
        max_workers=4,
        full=False,
    )
    assert report.status == "ok"
    assert report.mode == "smart"
    assert report.price_symbols == 300
    assert report.cache_path == "test.duckdb"


def test_sync_preserves_upstream_unavailable_reason() -> None:
    """miniQMT 不可用时，调用方能获得可判定的同步失败原因。"""
    ingestion = MagicMock()
    ingestion.run.return_value = {"status": "error", "reason": "xtquant_unavailable"}

    with patch(
        "long_earn.services.incremental_sync.DataIngestionService",
        return_value=ingestion,
    ):
        report = IncrementalSyncService().sync()

    assert report.status == "error"
    assert report.reason == "xtquant_unavailable"
