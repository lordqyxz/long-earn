"""Executor 逃生口单元测试（ADR-016 阶段 2）

验证算子缺口检测 → 研发 → 重试 / 研发失败 → 返回错误的契约。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from long_earn.strategy_rd.escape_hatch import (
    _create_operator_spec,
    _infer_category,
    _infer_input_fields,
    apply_escape_hatch_on_error,
    attempt_operator_development,
    detect_missing_operator,
    escape_hatch_with_retry,
)

# ── detect_missing_operator ───────────────────────────────────────


class TestDetectMissingOperator:
    """算子缺失错误检测契约"""

    def test_standard_error_message(self) -> None:
        """标准格式：ValueError("未知算子 'xxx'")"""
        error = ValueError("未知算子 'alpha_decay'")
        assert detect_missing_operator(error) == "alpha_decay"

    def test_double_quote_variant(self) -> None:
        """双引号变体：未知算子 "xxx\""""
        error = ValueError('未知算子 "momentum_factor"')
        assert detect_missing_operator(error) == "momentum_factor"

    def test_error_in_context(self) -> None:
        """错误嵌入在更长消息中"""
        error = ValueError("回测执行失败: 未知算子 'sharpe_weighted' 在步骤 3")
        assert detect_missing_operator(error) == "sharpe_weighted"

    def test_non_operator_error(self) -> None:
        """非算子缺失错误返回 None"""
        error = ValueError("YAML 语法错误: 缺少缩进")
        assert detect_missing_operator(error) is None

    def test_generic_exception(self) -> None:
        """非 ValueError 异常"""
        error = RuntimeError("未知算子 'test'")
        assert detect_missing_operator(error) == "test"

    def test_empty_string(self) -> None:
        """空字符串"""
        assert detect_missing_operator(ValueError("")) is None


# ── _infer_category / _infer_input_fields ─────────────────────────


class TestInferHelpers:
    """算子类别与输入字段推断"""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("stop_loss", "filter"),
            ("take_profit", "filter"),
            ("trend_filter", "filter"),
            ("top_n_rank", "rank"),
            ("bottom_select", "rank"),
            ("macd_signal", "technical"),
            ("bollinger_band", "technical"),
            ("rsi_divergence", "technical"),
            ("momentum_factor", "factor"),
            ("alpha_decay", "factor"),
        ],
    )
    def test_infer_category(self, name: str, expected: str) -> None:
        assert _infer_category(name) == expected

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("close_price", ["close"]),
            ("volume_weighted", ["close", "volume"]),
            ("volatility_index", ["close", "volume"]),
            ("alpha_decay", ["close"]),
        ],
    )
    def test_infer_input_fields(self, name: str, expected: list[str]) -> None:
        assert _infer_input_fields(name) == expected


# ── _create_operator_spec ─────────────────────────────────────────


class TestCreateOperatorSpec:
    """OperatorSpec 创建契约"""

    def test_spec_has_required_fields(self) -> None:
        spec = _create_operator_spec(
            operator_name="alpha_decay",
            strategy_yaml="strategy: test\noperators: [...]",
            hypothesis="测试 alpha 衰减假设",
        )
        assert spec.name == "alpha_decay"
        assert spec.reference_strategy  # 非空
        assert spec.motivation  # 非空
        assert spec.priority.value == "high"

    def test_spec_truncates_reference_strategy(self) -> None:
        long_yaml = "x" * 1000
        spec = _create_operator_spec(
            operator_name="test_op",
            strategy_yaml=long_yaml,
            hypothesis="h",
        )
        assert len(spec.reference_strategy) <= 500

    def test_spec_empty_yaml_raises(self) -> None:
        """reference_strategy 强制非空（OperatorSpec.__post_init__）"""
        with pytest.raises(ValueError, match="reference_strategy"):
            _create_operator_spec(
                operator_name="test_op",
                strategy_yaml="",
                hypothesis="h",
            )


# ── apply_escape_hatch_on_error ───────────────────────────────────


class TestApplyEscapeHatchOnError:
    """逃生口入口：错误路由契约"""

    def test_non_operator_error_returns_error_dict(self) -> None:
        """非算子缺失错误直接返回 error dict"""
        error = ValueError("YAML 语法错误")
        ctx = MagicMock()

        result = apply_escape_hatch_on_error(
            error=error,
            strategy_yaml="yaml: test",
            hypothesis="测试假设",
            context=ctx,
        )

        assert result is not None
        assert "error" in result
        assert result.get("escape_hatch_triggered") is False

    def test_operator_gap_with_no_backlog_returns_error(self) -> None:
        """算子缺失但 backlog 未初始化"""
        error = ValueError("未知算子 'missing_op'")
        ctx = MagicMock()
        ctx.operator_backlog = None

        result = apply_escape_hatch_on_error(
            error=error,
            strategy_yaml="yaml: test",
            hypothesis="测试假设",
            context=ctx,
        )

        assert result is not None
        assert "error" in result
        assert result.get("escape_hatch_triggered") is True

    @patch("long_earn.strategy_rd.escape_hatch.list_operators")
    @patch("long_earn.strategy_rd.escape_hatch.create_operator_dev_subgraph")
    def test_operator_development_success_returns_none(
        self,
        mock_create_subgraph: MagicMock,
        mock_list_ops: MagicMock,
    ) -> None:
        """算子研发成功返回 None（调用方应重试）"""
        error = ValueError("未知算子 'new_factor'")

        # 第一次 list_operators 返回空（算子不存在）
        # 第二次 list_operators 返回包含新算子（研发成功）
        mock_list_ops.side_effect = [
            {},  # 初始检查：算子不存在
            {"new_factor": {}},  # 研发后检查：算子已注册
        ]

        mock_subgraph = MagicMock()
        mock_create_subgraph.return_value = mock_subgraph

        ctx = MagicMock()
        ctx.operator_backlog = MagicMock()
        ctx.operator_backlog.submit.return_value = True

        result = apply_escape_hatch_on_error(
            error=error,
            strategy_yaml="yaml: test",
            hypothesis="测试假设",
            context=ctx,
        )

        # 返回 None 表示算子已就绪，调用方应重试
        assert result is None
        mock_subgraph.invoke.assert_called_once()


# ── escape_hatch_with_retry ───────────────────────────────────────


class TestEscapeHatchWithRetry:
    """逃生口 + 重试完整流程契约"""

    @patch("long_earn.strategy_rd.escape_hatch.list_operators")
    @patch("long_earn.strategy_rd.escape_hatch.create_operator_dev_subgraph")
    def test_full_success_path(
        self,
        mock_create_subgraph: MagicMock,
        mock_list_ops: MagicMock,
    ) -> None:
        """算子缺失 → 研发成功 → 重试 develop + backtest 成功"""
        error = ValueError("未知算子 'new_alpha'")

        mock_list_ops.side_effect = [
            {},  # 初始：算子不存在
            {"new_alpha": {}},  # 研发后：已注册
        ]
        mock_create_subgraph.return_value = MagicMock()

        ctx = MagicMock()
        ctx.operator_backlog = MagicMock()
        ctx.operator_backlog.submit.return_value = True

        develop_func = MagicMock(return_value="new_yaml: test")
        backtest_func = MagicMock(return_value={"sharpe_ratio": 1.5})

        result = escape_hatch_with_retry(
            error=error,
            strategy_yaml="old_yaml: test",
            optimized={"name": "strategy"},
            hypothesis="测试 alpha 假设",
            context=ctx,
            develop_func=develop_func,
            backtest_func=backtest_func,
        )

        assert result.get("escape_hatch_triggered") is True
        assert result.get("strategy_yaml") == "new_yaml: test"
        assert result.get("backtest_result") == {"sharpe_ratio": 1.5}
        develop_func.assert_called_once()
        backtest_func.assert_called_once()

    @patch("long_earn.strategy_rd.escape_hatch.list_operators")
    @patch("long_earn.strategy_rd.escape_hatch.create_operator_dev_subgraph")
    def test_development_failure_returns_error(
        self,
        mock_create_subgraph: MagicMock,
        mock_list_ops: MagicMock,
    ) -> None:
        """算子缺失 → 研发失败 → 返回错误"""
        error = ValueError("未知算子 'bad_op'")

        # 算子始终不存在（研发失败/blocked）
        mock_list_ops.return_value = {}
        mock_create_subgraph.return_value = MagicMock()

        ctx = MagicMock()
        ctx.operator_backlog = MagicMock()
        ctx.operator_backlog.submit.return_value = True

        develop_func = MagicMock()
        backtest_func = MagicMock()

        result = escape_hatch_with_retry(
            error=error,
            strategy_yaml="yaml: test",
            optimized={"name": "strategy"},
            hypothesis="测试假设",
            context=ctx,
            develop_func=develop_func,
            backtest_func=backtest_func,
        )

        assert "error" in result
        assert result.get("escape_hatch_triggered") is True
        # develop/backtest 不应被调用
        develop_func.assert_not_called()
        backtest_func.assert_not_called()

    def test_non_operator_error_returns_error(self) -> None:
        """非算子缺失错误直接返回（不触发研发）"""
        error = ValueError("YAML 解析失败")
        ctx = MagicMock()

        develop_func = MagicMock()
        backtest_func = MagicMock()

        result = escape_hatch_with_retry(
            error=error,
            strategy_yaml="yaml: test",
            optimized={},
            hypothesis="测试假设",
            context=ctx,
            develop_func=develop_func,
            backtest_func=backtest_func,
        )

        assert "error" in result
        assert result.get("escape_hatch_triggered") is False
        develop_func.assert_not_called()
        backtest_func.assert_not_called()

    @patch("long_earn.strategy_rd.escape_hatch.list_operators")
    @patch("long_earn.strategy_rd.escape_hatch.create_operator_dev_subgraph")
    def test_retry_failure_returns_error(
        self,
        mock_create_subgraph: MagicMock,
        mock_list_ops: MagicMock,
    ) -> None:
        """算子研发成功但重试 backtest 失败"""
        error = ValueError("未知算子 'new_beta'")

        mock_list_ops.side_effect = [
            {},  # 初始：不存在
            {"new_beta": {}},  # 研发后：已注册
        ]
        mock_create_subgraph.return_value = MagicMock()

        ctx = MagicMock()
        ctx.operator_backlog = MagicMock()
        ctx.operator_backlog.submit.return_value = True

        develop_func = MagicMock(side_effect=RuntimeError("LLM 不可用"))

        result = escape_hatch_with_retry(
            error=error,
            strategy_yaml="yaml: test",
            optimized={"name": "strategy"},
            hypothesis="测试假设",
            context=ctx,
            develop_func=develop_func,
            backtest_func=MagicMock(),
        )

        assert "error" in result
        assert result.get("escape_hatch_triggered") is True
        assert "重试失败" in result["error"]


# ── attempt_operator_development ──────────────────────────────────


class TestAttemptOperatorDevelopment:
    """算子研发函数契约"""

    @patch("long_earn.strategy_rd.escape_hatch.list_operators")
    def test_operator_already_exists(self, mock_list_ops: MagicMock) -> None:
        """算子已存在直接返回成功"""
        mock_list_ops.return_value = {"existing_op": {}}
        ctx = MagicMock()

        result = attempt_operator_development(
            operator_name="existing_op",
            strategy_yaml="yaml: test",
            hypothesis="测试假设",
            context=ctx,
        )

        assert result.success is True
        assert result.operator_name == "existing_op"
        # 不应调用 backlog
        ctx.operator_backlog.submit.assert_not_called()

    def test_no_backlog_returns_failure(self) -> None:
        """backlog 未初始化返回失败"""
        ctx = MagicMock()
        ctx.operator_backlog = None

        with patch(
            "long_earn.strategy_rd.escape_hatch.list_operators",
            return_value={},
        ):
            result = attempt_operator_development(
                operator_name="missing_op",
                strategy_yaml="yaml: test",
                hypothesis="测试假设",
                context=ctx,
            )

        assert result.success is False
        assert "OperatorBacklog" in result.error
