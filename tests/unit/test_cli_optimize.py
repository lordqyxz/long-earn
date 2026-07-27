"""optimize CLI 子命令参数解析测试（ADR-009 收尾）。

仅验证 CLI 参数解析与命令注册，不调用真实 LLM/backtest（避免网络/数据依赖）。
"""

from __future__ import annotations

from typer.testing import CliRunner

from long_earn.cli import app

runner = CliRunner()


def test_optimize_command_registered() -> None:
    """optimize 子命令已注册，--help 能正常显示。"""
    result = runner.invoke(app, ["optimize", "--help"])
    assert result.exit_code == 0
    assert "优化已有策略" in result.stdout or "离线策略优化" in result.stdout
    assert "--strategy-yaml" in result.stdout
    assert "--suggestions" in result.stdout


def test_optimize_command_default_strategy_path() -> None:
    """默认 --strategy-yaml 指向 best_strategy.yaml。"""
    result = runner.invoke(app, ["optimize", "--help"])
    assert result.exit_code == 0
    # 默认值在 help 中显示为 [default: best_strategy.yaml]
    assert "best_strategy.yaml" in result.stdout


def test_optimize_command_max_iterations_option() -> None:
    """--max-iterations 选项已注册。"""
    result = runner.invoke(app, ["optimize", "--help"])
    assert result.exit_code == 0
    assert "--max-iterations" in result.stdout
