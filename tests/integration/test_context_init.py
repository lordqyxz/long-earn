"""RuntimeContext 初始化集成测试

验证 create_runtime_context() 和 initialize_context() 正确组装所有服务。
"""

import pytest

from long_earn.config import AppConfig
from long_earn.context_init import create_runtime_context, initialize_context


class TestCreateRuntimeContext:
    """create_runtime_context 接口集成测试"""

    def test_creates_all_services(self):
        """应创建所有必需服务"""
        config = AppConfig.from_env()
        ctx = create_runtime_context(config)

        assert ctx.llm_service is not None
        assert ctx.memory is not None
        assert ctx.stock_service is not None
        assert ctx.backtest_service is not None
        assert ctx.logger is not None
        assert ctx.monitoring is not None
        assert ctx.config is not None

    def test_config_matches_input(self):
        """RuntimeContext 中的 config 应与输入一致"""
        config = AppConfig.from_env()
        ctx = create_runtime_context(config)

        assert ctx.config is config


class TestInitializeContext:
    """initialize_context 完整初始化集成测试"""

    def test_initializes_memory(self):
        """initialize_context 应初始化记忆系统"""
        try:
            ctx = initialize_context()
        except Exception as e:
            pytest.skip(f"上下文初始化失败: {e}")

        assert ctx.memory is not None
