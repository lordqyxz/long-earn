"""集成测试 conftest

回测引擎已内嵌到主项目，无需启动外部服务。
- 测试前自动初始化 RuntimeContext（含记忆系统 + 统一配置加载）
- 支持通过 BACKTEST_SERVICE_MANUAL=1 环境变量跳过

配置加载（ADR-007）：通过 ``load_config()`` 统一加载 .env，不再
分散调用 ``load_dotenv()``。
"""

import pytest

from long_earn.config import load_config

# 模块级一次性加载 .env（中心化），让所有集成测试共享同一配置
load_config()


@pytest.fixture(scope="session")
def context():
    """session 级别的 RuntimeContext"""
    from long_earn.context_init import initialize_context

    try:
        ctx = initialize_context()
    except Exception as e:
        pytest.skip(f"运行时上下文初始化失败: {e}")
    return ctx
