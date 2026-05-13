"""AppConfig 和 RuntimeContext 测试"""

from unittest.mock import MagicMock

from long_earn.config import AppConfig, RuntimeContext


class TestAppConfigDefaults:
    def test_default_values(self):
        config = AppConfig()
        assert config.llm_type == "ollama"
        assert config.llm_model == "qwen3.5:cloud"
        assert config.max_iterations == 3
        assert config.backtest_start_date == "2020-01-01"
        assert config.backtest_end_date == "2023-12-31"

    def test_custom_values(self):
        config = AppConfig(
            llm_type="openai",
            llm_model="gpt-4",
            max_iterations=5,
            backtest_start_date="2023-01-01",
            backtest_end_date="2024-12-31",
        )
        assert config.llm_type == "openai"
        assert config.llm_model == "gpt-4"
        assert config.max_iterations == 5


class TestAppConfigFromEnv:
    def test_from_env_defaults(self, monkeypatch):
        for key in [
            "LLM_TYPE",
            "LLM_MODEL",
            "LLM_BASE_URL",
            "MEMORY_PATH",
            "INIT_DIR",
            "MAX_ITERATIONS",
            "BACKTEST_START_DATE",
            "BACKTEST_END_DATE",
            "STRATEGY_KEYWORDS",
            "STOCK_ANALYSIS_KEYWORDS",
        ]:
            monkeypatch.delenv(key, raising=False)

        config = AppConfig.from_env()
        assert config.llm_type == "ollama"
        assert config.max_iterations == 3
        assert config.strategy_keywords == ("策略", "思路", "投资策略")
        assert config.stock_analysis_keywords == ("股票", "分析", "公司")

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

    def test_from_env_keywords_empty(self, monkeypatch):
        monkeypatch.setenv("STRATEGY_KEYWORDS", ",")
        monkeypatch.setenv("STOCK_ANALYSIS_KEYWORDS", "")
        monkeypatch.delenv("LLM_TYPE", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.delenv("MEMORY_PATH", raising=False)
        monkeypatch.delenv("INIT_DIR", raising=False)
        monkeypatch.delenv("MAX_ITERATIONS", raising=False)
        monkeypatch.delenv("BACKTEST_START_DATE", raising=False)
        monkeypatch.delenv("BACKTEST_END_DATE", raising=False)

        config = AppConfig.from_env()
        assert config.strategy_keywords == ()
        assert config.stock_analysis_keywords == ()


class TestAppConfigValidate:
    def test_valid_config(self):
        config = AppConfig()
        errors = config.validate()
        assert errors == []

    def test_invalid_llm_type(self):
        config = AppConfig(llm_type="unknown_provider")
        errors = config.validate()
        assert len(errors) >= 1
        assert any("LLM 类型" in e for e in errors)

    def test_invalid_max_iterations(self):
        config = AppConfig(max_iterations=0)
        errors = config.validate()
        assert len(errors) >= 1
        assert any("迭代次数" in e for e in errors)

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
