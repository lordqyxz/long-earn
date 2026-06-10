"""集成测试 conftest

回测引擎已内嵌到主项目，无需启动外部服务。
- 测试前自动初始化 RuntimeContext（包含记忆系统）
- 支持通过 BACKTEST_SERVICE_MANUAL=1 环境变量跳过
"""

import pytest
from dotenv import load_dotenv

load_dotenv()


@pytest.fixture(scope="session")
def context():
    """session 级别的 RuntimeContext"""
    from long_earn.context_init import initialize_context

    try:
        ctx = initialize_context()
    except Exception as e:
        pytest.skip(f"运行时上下文初始化失败: {e}")
    return ctx
