"""FastAPI visualization public-interface tests."""

from pathlib import Path

import pytest

from long_earn.app import serve_visualization_fastapi
from long_earn.app.app import _create_app, _is_loopback_host
from long_earn.core.pg import pg_version


def _pg_available() -> bool:
    """探测 PostgreSQL 是否可连（不可达时 _create_app 相关测试跳过）。"""
    try:
        pg_version()
        return True
    except Exception:
        return False


# _create_app 构造会初始化 BacktestAnalyzer / EventAnalyzer（连 PG），
# PG 不可达时这些测试无法运行。
_PG_REQUIRED = pytest.mark.skipif(
    not _pg_available(), reason="PostgreSQL 服务不可用"
)


def test_fastapi_visualization_entrypoint_is_callable() -> None:
    """The public server entrypoint remains available to CLI callers."""
    assert callable(serve_visualization_fastapi)


@_PG_REQUIRED
def test_fastapi_app_registers_core_run_routes(tmp_path: Path) -> None:
    """The application exposes the established backtest REST interface."""
    app = _create_app()
    routes = {route.path for route in app.routes}
    assert "/api/health" in routes
    assert "/api/runs" in routes
    assert "/api/runs/{run_id}/dashboard" in routes


def test_loopback_hosts_are_recognized() -> None:
    """The server only accepts local binds by default."""
    assert _is_loopback_host("127.0.0.1")
    assert _is_loopback_host("::1")
    assert _is_loopback_host("localhost")
    assert not _is_loopback_host("0.0.0.0")


def test_remote_bind_requires_explicit_opt_in() -> None:
    """An externally reachable host fails closed without authorization."""
    with pytest.raises(ValueError, match="allow_remote=True"):
        serve_visualization_fastapi(host="0.0.0.0")


@_PG_REQUIRED
def test_remote_bind_runs_only_with_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """The opt-in reaches Uvicorn and marks the app as remote."""
    captured: dict[str, object] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr("long_earn.app.app.uvicorn.run", fake_run)

    serve_visualization_fastapi(host="0.0.0.0", allow_remote=True)

    assert captured["host"] == "0.0.0.0"
    assert captured["app"].state.remote_mode is True  # type: ignore[union-attr]
