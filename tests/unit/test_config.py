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
        monkeypatch.setenv("EMBEDDING_MODEL", "bge-m3")
        monkeypatch.setenv("EMBEDDING_BASE_URL", "http://localhost:11434")

        config = AppConfig.from_env()
        assert config.llm_type == "openai"
        assert config.llm_model == "gpt-4"
        assert config.max_iterations == 10
        assert config.strategy_keywords == ("alpha", "beta")
        assert config.stock_analysis_keywords == ("财报",)
        assert config.embedding_model == "bge-m3"
        assert config.embedding_base_url == "http://localhost:11434"

    def test_embedding_config_defaults(self):
        """embedding 配置应有合理的默认值"""
        config = AppConfig()
        assert config.embedding_model == "bge-m3"
        assert config.reranker_model == "bge-reranker-v2-m3"
        assert config.embedding_base_url == ""


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


class TestLoadConfig:
    """load_config 中心化加载契约（ADR-007）"""

    def test_load_config_returns_appconfig_with_no_env_file(
        self, monkeypatch, tmp_path
    ):
        """无 .env 文件时仍应正常返回（用 AppConfig 默认值 + 当前环境变量）"""
        from long_earn.config import load_config

        # 清掉可能影响默认值的环境变量
        monkeypatch.delenv("LLM_TYPE", raising=False)
        monkeypatch.delenv("LONG_EARN_ENV", raising=False)
        cfg = load_config(search_from=tmp_path)
        assert cfg.llm_type == "ollama"  # AppConfig 默认值

    def test_load_config_reads_default_dotenv(self, monkeypatch, tmp_path):
        """search_from 下存在 .env 时应自动加载其内容"""
        from long_earn.config import load_config

        (tmp_path / ".env").write_text("LLM_TYPE=openai\nMAX_ITERATIONS=7\n")
        monkeypatch.delenv("LLM_TYPE", raising=False)
        monkeypatch.delenv("MAX_ITERATIONS", raising=False)
        monkeypatch.delenv("LONG_EARN_ENV", raising=False)
        cfg = load_config(search_from=tmp_path)
        assert cfg.llm_type == "openai"
        assert cfg.max_iterations == 7

    def test_load_config_long_earn_env_selects_file(self, monkeypatch, tmp_path):
        """LONG_EARN_ENV=dev → 应加载 .env.dev 而非 .env"""
        from long_earn.config import load_config

        (tmp_path / ".env").write_text("LLM_TYPE=ollama\n")
        (tmp_path / ".env.dev").write_text("LLM_TYPE=dashscope\n")
        monkeypatch.delenv("LLM_TYPE", raising=False)
        monkeypatch.setenv("LONG_EARN_ENV", "dev")
        cfg = load_config(search_from=tmp_path)
        assert cfg.llm_type == "dashscope"

    def test_load_config_long_earn_env_missing_falls_back(
        self, monkeypatch, tmp_path
    ):
        """LONG_EARN_ENV 指向不存在的文件时回退默认 .env"""
        from long_earn.config import load_config

        (tmp_path / ".env").write_text("LLM_TYPE=ollama\nLLM_MODEL=fallback-model\n")
        monkeypatch.delenv("LLM_TYPE", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.setenv("LONG_EARN_ENV", "nonexistent")
        cfg = load_config(search_from=tmp_path)
        assert cfg.llm_model == "fallback-model"

    def test_load_config_os_environ_overrides_dotenv(self, monkeypatch, tmp_path):
        """默认 override=False：已设的 os.environ 优先于 .env 文件"""
        from long_earn.config import load_config

        (tmp_path / ".env").write_text("LLM_TYPE=openai\n")
        # 显式环境变量应胜出
        monkeypatch.setenv("LLM_TYPE", "ollama")
        monkeypatch.delenv("LONG_EARN_ENV", raising=False)
        cfg = load_config(search_from=tmp_path)
        assert cfg.llm_type == "ollama"

    def test_load_config_explicit_env_file_takes_precedence(
        self, monkeypatch, tmp_path
    ):
        """显式 env_file 优先级最高（覆盖 LONG_EARN_ENV 与默认 .env）"""
        from long_earn.config import load_config

        (tmp_path / ".env").write_text("LLM_TYPE=ollama\n")
        (tmp_path / ".env.dev").write_text("LLM_TYPE=dashscope\n")
        custom = tmp_path / "custom.env"
        custom.write_text("LLM_TYPE=openai\n")
        monkeypatch.delenv("LLM_TYPE", raising=False)
        monkeypatch.setenv("LONG_EARN_ENV", "dev")
        cfg = load_config(env_file=custom, search_from=tmp_path)
        assert cfg.llm_type == "openai"
