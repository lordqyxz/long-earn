"""AppConfig 和 RuntimeContext 测试"""

from unittest.mock import MagicMock

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


class TestRuntimeContext:
    def test_construction(self):
        mock_llm = MagicMock()
        mock_memory = MagicMock()
        mock_stock = MagicMock()
        mock_backtest = MagicMock()
        mock_logger = MagicMock()
        mock_monitoring = MagicMock()

        config = AppConfig()
        ctx = RuntimeContext(
            llm_service=mock_llm,
            memory=mock_memory,
            stock_service=mock_stock,
            backtest_service=mock_backtest,
            logger=mock_logger,
            monitoring=mock_monitoring,
            config=config,
        )
        assert ctx.llm_service is mock_llm
        assert ctx.config is config
        assert ctx.config.llm_type == "ollama"
