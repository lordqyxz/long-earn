"""Executor 失败路径逃生口单元测试（ADR-016 阶段 3）

验证失败分类（规则先行、LLM 兜底，ADR-021）→ refine 重试 / directional prune 契约。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from long_earn.strategy_rd.escape_hatch import (
    classify_failure_type,
    escape_hatch_failure_path,
)

# ── classify_failure_type ────────────────────────────────────────


class TestClassifyFailureType:
    """失败类型分类契约（ADR-021：规则先行、LLM 兜底）"""

    def test_rule_matched_error_skips_llm(self) -> None:
        """确定性可判定异常（ValueError 等）直接 fixable，不消耗 LLM"""
        llm = MagicMock()

        result = classify_failure_type(
            error=ValueError("YAML 缩进错误"),
            hypothesis="测试假设",
            llm_service=llm,
        )
        assert result == "fixable"
        llm.invoke.assert_not_called()

    def test_rule_matched_yaml_error_skips_llm(self) -> None:
        import yaml

        llm = MagicMock()

        result = classify_failure_type(
            error=yaml.YAMLError("mapping values not allowed"),
            hypothesis="测试假设",
            llm_service=llm,
        )
        assert result == "fixable"
        llm.invoke.assert_not_called()

    def test_llm_fallback_directional_for_unrulable_error(self) -> None:
        """规则未命中（RuntimeError）时走 LLM，可判 directional"""
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content="directional")

        result = classify_failure_type(
            error=RuntimeError("策略逻辑根本性失效"),
            hypothesis="动量因子假设",
            llm_service=llm,
        )
        assert result == "directional"
        llm.invoke.assert_called_once()

    def test_llm_fallback_case_insensitive(self) -> None:
        """大小写不敏感"""
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content="DIRECTIONAL")

        result = classify_failure_type(
            error=RuntimeError("假设不合理"),
            hypothesis="测试假设",
            llm_service=llm,
        )
        assert result == "directional"

    def test_defaults_to_fixable_on_llm_failure(self) -> None:
        """LLM 调用失败时默认 fixable（安全降级）"""
        llm = MagicMock()
        llm.invoke.side_effect = RuntimeError("LLM 不可用")

        result = classify_failure_type(
            error=RuntimeError("某错误"),
            hypothesis="测试假设",
            llm_service=llm,
        )
        assert result == "fixable"

    def test_handles_response_without_content(self) -> None:
        """response 没有 content 属性时降级为 str()"""
        llm = MagicMock()
        llm.invoke.return_value = "fixable"

        result = classify_failure_type(
            error=RuntimeError("某错误"),
            hypothesis="测试假设",
            llm_service=llm,
        )
        assert result == "fixable"


# ── escape_hatch_failure_path ─────────────────────────────────────


class TestEscapeHatchFailurePath:
    """失败路径逃生口完整流程契约"""

    @patch("long_earn.strategy_rd.escape_hatch.classify_failure_type")
    def test_fixable_refine_success(
        self,
        mock_classify: MagicMock,
    ) -> None:
        """fixable → refine 成功 → 返回 backtest 结果"""
        mock_classify.return_value = "fixable"

        refine_func = MagicMock(return_value="refined_yaml: test")
        backtest_func = MagicMock(return_value={"sharpe_ratio": 1.2})

        result = escape_hatch_failure_path(
            error=ValueError("YAML 缩进错误"),
            strategy_yaml="bad_yaml: test",
            optimized={"name": "strategy"},
            hypothesis="测试假设",
            llm_service=MagicMock(),
            refine_func=refine_func,
            backtest_func=backtest_func,
        )

        assert result.get("escape_hatch_triggered") is True
        assert result.get("failure_path") == "fixable"
        assert result.get("strategy_yaml") == "refined_yaml: test"
        assert result.get("backtest_result") == {"sharpe_ratio": 1.2}
        refine_func.assert_called_once()
        backtest_func.assert_called_once()

    @patch("long_earn.strategy_rd.escape_hatch.classify_failure_type")
    def test_directional_returns_error(
        self,
        mock_classify: MagicMock,
    ) -> None:
        """directional → 直接返回错误（不 refine）"""
        mock_classify.return_value = "directional"

        refine_func = MagicMock()
        backtest_func = MagicMock()

        result = escape_hatch_failure_path(
            error=ValueError("因子在训练集无信号"),
            strategy_yaml="yaml: test",
            optimized={"name": "strategy"},
            hypothesis="动量因子假设",
            llm_service=MagicMock(),
            refine_func=refine_func,
            backtest_func=backtest_func,
        )

        assert "error" in result
        assert result.get("escape_hatch_triggered") is True
        assert result.get("failure_path") == "directional"
        assert "方向性失败" in result["error"]
        # 不应调用 refine/backtest
        refine_func.assert_not_called()
        backtest_func.assert_not_called()

    @patch("long_earn.strategy_rd.escape_hatch.classify_failure_type")
    def test_fixable_refine_failure_returns_error(
        self,
        mock_classify: MagicMock,
    ) -> None:
        """fixable → refine 失败 → 返回错误"""
        mock_classify.return_value = "fixable"

        refine_func = MagicMock(side_effect=RuntimeError("LLM 不可用"))

        result = escape_hatch_failure_path(
            error=ValueError("YAML 语法错误"),
            strategy_yaml="bad_yaml: test",
            optimized={"name": "strategy"},
            hypothesis="测试假设",
            llm_service=MagicMock(),
            refine_func=refine_func,
            backtest_func=MagicMock(),
        )

        assert "error" in result
        assert result.get("escape_hatch_triggered") is True
        assert result.get("failure_path") == "fixable"
        assert "refine 重试失败" in result["error"]

    @patch("long_earn.strategy_rd.escape_hatch.classify_failure_type")
    def test_fixable_backtest_failure_returns_error(
        self,
        mock_classify: MagicMock,
    ) -> None:
        """fixable → refine 成功但 backtest 失败 → 返回错误"""
        mock_classify.return_value = "fixable"

        refine_func = MagicMock(return_value="refined_yaml: test")
        backtest_func = MagicMock(side_effect=ValueError("回测引擎崩溃"))

        result = escape_hatch_failure_path(
            error=ValueError("YAML 参数错误"),
            strategy_yaml="bad_yaml: test",
            optimized={"name": "strategy"},
            hypothesis="测试假设",
            llm_service=MagicMock(),
            refine_func=refine_func,
            backtest_func=backtest_func,
        )

        assert "error" in result
        assert result.get("escape_hatch_triggered") is True
        assert result.get("failure_path") == "fixable"
        assert "refine 重试失败" in result["error"]

    @patch("long_earn.strategy_rd.escape_hatch.classify_failure_type")
    def test_audit_log_present(
        self,
        mock_classify: MagicMock,
    ) -> None:
        """审计日志记录分类结果"""
        mock_classify.return_value = "directional"

        result = escape_hatch_failure_path(
            error=ValueError("假设不合理"),
            strategy_yaml="yaml: test",
            optimized={"name": "strategy"},
            hypothesis="测试假设",
            llm_service=MagicMock(),
            refine_func=MagicMock(),
            backtest_func=MagicMock(),
        )

        assert "escape_hatch_audit" in result
        assert "directional" in result["escape_hatch_audit"]
