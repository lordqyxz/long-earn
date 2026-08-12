"""AppConfig 和 RuntimeContext 测试"""

from unittest.mock import MagicMock

import pytest

from long_earn.config import AppConfig, RuntimeContext


class TestAppConfigFromEnv:
    def test_from_env_custom(self, monkeypatch):
        monkeypatch.setenv("LLM_TYPE", "openai")
        monkeypatch.setenv("LLM_MODEL", "gpt-4")
        monkeypatch.setenv("MAX_ITERATIONS", "10")
        monkeypatch.setenv("STRATEGY_KEYWORDS", "alpha,beta")
        monkeypatch.setenv("STOCK_ANALYSIS_KEYWORDS", "财报")

        config = AppConfig.from_env()
        assert config.llm_type == "openai"
        assert config.llm_model == "gpt-4"
        assert config.max_iterations == 10
        assert config.strategy_keywords == ("alpha", "beta")
        assert config.stock_analysis_keywords == ("财报",)


class TestAppConfigValidate:
    def test_valid_config(self):
        config = AppConfig()
        errors = config.validate()
        assert errors == []

    def test_multiple_errors(self):
        config = AppConfig(llm_type="bad", max_iterations=-1)
        errors = config.validate()
        assert len(errors) == 2

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            (
                {"train_start_date": "2025-01-02", "train_end_date": "2025-01-01"},
                "训练集日期倒序",
            ),
            ({"test_start_date": "2024-12-31"}, "训练集与测试集必须严格有序且不重叠"),
            (
                {"validation_start_date": "2026-03-24"},
                "测试集与验证集必须严格有序且不重叠",
            ),
            ({"train_start_date": "2022/01/01"}, "TRAIN_START 必须是 YYYY-MM-DD"),
            (
                {"test_start_date": "2025-01-01", "test_end_date": "2025-01-01"},
                "测试集日期倒序",
            ),
        ],
    )
    def test_rejects_invalid_data_splits(self, overrides, message):
        config = AppConfig(**overrides)

        errors = config.validate()

        assert any(message in error for error in errors)


class TestRuntimeContext:
    def test_construction(self):
        mock_llm = MagicMock()
        mock_memory = MagicMock()
        mock_stock = MagicMock()
        mock_backtest = MagicMock()
        mock_logger = MagicMock()
        mock_monitoring = MagicMock()
        mock_context_preparation = MagicMock()

        config = AppConfig()
        ctx = RuntimeContext(
            llm_service=mock_llm,
            memory=mock_memory,
            stock_service=mock_stock,
            backtest_service=mock_backtest,
            logger=mock_logger,
            monitoring=mock_monitoring,
            config=config,
            context_preparation=mock_context_preparation,
        )
        assert ctx.llm_service is mock_llm
        assert ctx.config is config
        assert ctx.config.llm_type == "deepseek"

        ctx.prepare_context("茅台", k=2, force_refresh=True)
        mock_context_preparation.prepare.assert_called_once_with(
            "茅台", k=2, force_refresh=True
        )
