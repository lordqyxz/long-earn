"""cache_sync：启动同步后保持 DuckDB 主数据层优先访问。"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from long_earn.services import cache_sync


def test_sync_data_cache_does_not_set_cache_only_on_success() -> None:
    os.environ.pop(cache_sync.CACHE_ONLY_ENV, None)

    mock_client = MagicMock()
    mock_client.is_available = True

    mock_service = MagicMock()
    with (
        patch.object(cache_sync.MiniQmtClient, "get", return_value=mock_client),
        patch.object(cache_sync, "DataCache") as mock_cache_cls,
        patch(
            "long_earn.services.incremental_sync.IncrementalSyncService",
            return_value=mock_service,
        ),
    ):
        mock_cache_cls.return_value.db_path = "dummy.duckdb"
        mock_service.sync.return_value.as_dict.return_value = {
            "status": "ok",
            "price_symbols": 10,
            "financial_symbols": 10,
            "cache_path": "dummy.duckdb",
        }
        mock_service.sync.return_value.status = "ok"
        result = cache_sync.sync_data_cache(universe="csi300")

    assert result["status"] == "ok"
    assert not cache_sync.is_cache_only()


def test_sync_skips_when_xtquant_unavailable_without_locking() -> None:
    os.environ.pop(cache_sync.CACHE_ONLY_ENV, None)

    mock_client = MagicMock()
    mock_client.is_available = False

    with (
        patch.object(cache_sync.MiniQmtClient, "get", return_value=mock_client),
        patch.object(cache_sync, "DataCache") as mock_cache_cls,
    ):
        mock_cache_cls.return_value.db_path = "dummy.duckdb"
        result = cache_sync.sync_data_cache()

    assert result["status"] == "skipped"
    assert result["reason"] == "xtquant_unavailable"
    assert not cache_sync.is_cache_only()


def test_clear_cache_only_restores_ondemand_path() -> None:
    cache_sync.set_cache_only()
    assert cache_sync.is_cache_only()
    cache_sync.clear_cache_only()
    assert not cache_sync.is_cache_only()
