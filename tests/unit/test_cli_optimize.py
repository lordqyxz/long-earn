"""optimize CLI 子命令参数解析测试（ADR-009 收尾）。

仅验证 CLI 参数解析与命令注册，不调用真实 LLM/backtest（避免网络/数据依赖）。
"""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from long_earn.cli import app

runner = CliRunner()
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    """去掉 Rich/ANSI 着色，避免 ``--strategy-yaml`` 被拆成不可连续匹配的片段。"""
    return _ANSI_RE.sub("", text)


def test_optimize_command_registered() -> None:
    """optimize 子命令已注册，--help 能正常显示。"""
    result = runner.invoke(app, ["optimize", "--help"], color=False)
    assert result.exit_code == 0
    stdout = _plain(result.stdout)
    assert "优化已有策略" in stdout or "离线策略优化" in stdout
    assert "--strategy-yaml" in stdout
    assert "--suggestions" in stdout


def test_optimize_command_default_strategy_path() -> None:
    """默认 --strategy-yaml 指向 best_strategy.yaml。"""
    result = runner.invoke(app, ["optimize", "--help"], color=False)
    assert result.exit_code == 0
    # 默认值在 help 中显示为 [default: best_strategy.yaml]
    assert "best_strategy.yaml" in _plain(result.stdout)


def test_optimize_command_max_iterations_option() -> None:
    """--max-iterations 选项已注册。"""
    result = runner.invoke(app, ["optimize", "--help"], color=False)
    assert result.exit_code == 0
    assert "--max-iterations" in _plain(result.stdout)


def test_web_command_defaults_to_loopback_and_forwards_remote_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI exposes an explicit opt-in before passing a remote bind onward."""
    calls: list[dict[str, object]] = []

    def fake_serve(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(
        "long_earn.app.app.serve_visualization_fastapi", fake_serve
    )

    default_result = runner.invoke(app, ["web"], color=False)
    remote_result = runner.invoke(
        app, ["web", "--host", "0.0.0.0", "--allow-remote"], color=False
    )

    assert default_result.exit_code == 0
    assert remote_result.exit_code == 0
    assert calls == [
        {
            "host": "127.0.0.1",
            "port": 8090,
            "db_path": "",
            "substances_path": "",
            "allow_remote": False,
        },
        {
            "host": "0.0.0.0",
            "port": 8090,
            "db_path": "",
            "substances_path": "",
            "allow_remote": True,
        },
    ]
