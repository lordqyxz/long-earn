"""FastAPI visualization public-interface tests."""

from pathlib import Path

import pytest

from long_earn.app import serve_visualization_fastapi
from long_earn.app.app import _create_app, _is_loopback_host


def test_fastapi_visualization_entrypoint_is_callable() -> None:
    """The public server entrypoint remains available to CLI callers."""
    assert callable(serve_visualization_fastapi)


def test_fastapi_app_registers_core_run_routes(tmp_path: Path) -> None:
    """The application exposes the established backtest REST interface."""
    app = _create_app(db_path=tmp_path / "audit.duckdb")
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
