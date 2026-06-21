"""应用上下文管理模块

参考 LangGraph Context 实践，提供统一的上下文管理，用于传递配置和依赖。
使用直接属性访问提供最佳类型安全支持。

配置中心化（TODO #4.3）：
- ``load_config()`` 是配置加载唯一入口，封装"dotenv 加载 + AppConfig.from_env()"。
- 多环境支持：``LONG_EARN_ENV`` 选择 ``.env.<name>``（如 dev/staging/prod），
  缺失时回退到默认 ``.env``。
- 优先级：**显式 os.environ > 选定 .env 文件 > AppConfig 默认值**（dotenv 的
  ``override=False`` 行为）——生产环境通过环境变量覆盖 yaml/dotenv 是标准做法。
- 详见 [ADR-007](docs/adr/007-config-centralization.md)。
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

from long_earn.services import (
    BacktestService,
    LLMService,
    LoggerService,
    MemoryService,
    MonitoringService,
    StockService,
)

if TYPE_CHECKING:
    from long_earn.backtest.data.provider import DataProvider

_logger = logging.getLogger(__name__)

# 项目数据目录
_project_root = Path(__file__).parent.parent.parent.parent
PROJECT_DATA_DIR = _project_root / ".data"

# 多环境配置文件选择：LONG_EARN_ENV=dev|staging|prod 等 → .env.<name>
# 未设置则回退到默认 .env
_ENV_NAME_VAR = "LONG_EARN_ENV"


@dataclass
class RuntimeContext:
    """运行时上下文（DI Container）

    设计原则（Clean Architecture）：
    - **基础设施层**（config / logger / monitoring）：必填，最先就绪
    - **业务服务层**（llm / memory / stock / backtest）：必填，由 `create_runtime_context`
      一次性构造完毕注入。业务节点接收**非空**实例，无需 None 守卫。
    - **数据层**（data_provider）：可选，跨子图共享，并非所有路径都需要

    历史上业务服务字段类型曾是可空联合 + ``require_*()`` 访问器，
    用于支持「先建 ctx 再注入 services」的渐进构造。现 services 已解耦为接
    ``(config, logger)``，可在 ctx 构造前先建好，因此字段类型已收紧为非空。

    保留 ``require_*()`` 访问器供下游使用，等价于直接读字段（不再可能 None）。

    用法:
        ctx = create_runtime_context(config)  # 推荐
        response = ctx.llm_service.invoke(prompt)     # 直接访问即可
        response = ctx.require_llm().invoke(prompt)   # 等价写法（向后兼容）
    """

    # 基础设施（必填）
    config: "AppConfig"
    logger: LoggerService
    monitoring: MonitoringService

    # 业务服务（必填，由 create_runtime_context 注入）
    llm_service: LLMService
    memory: MemoryService
    stock_service: StockService
    backtest_service: BacktestService

    # 数据层（可选）
    data_provider: "DataProvider | None" = None

    def require_llm(self) -> LLMService:
        """获取 LLM 服务（非空保证，等价于读 ``self.llm_service``）"""
        return self.llm_service

    def require_memory(self) -> MemoryService:
        """获取记忆服务（非空保证）"""
        return self.memory

    def require_stock(self) -> StockService:
        """获取股票服务（非空保证）"""
        return self.stock_service

    def require_backtest(self) -> BacktestService:
        """获取回测服务（非空保证）"""
        return self.backtest_service

    def require_data_provider(self) -> "DataProvider":
        """获取数据提供者，未注入时抛出明确错误"""
        if self.data_provider is None:
            raise RuntimeError("DataProvider 未初始化")
        return self.data_provider


@dataclass
class AppConfig:
    """应用配置

    Attributes:
        llm_type: LLM 类型，可选值：ollama, dashscope, openai
        llm_model: LLM 模型名称
        llm_base_url: LLM API 基础 URL
        init_dir: 知识库初始化目录
        memory_path: 记忆持久化路径
        max_iterations: 最大迭代次数
        backtest_start_date: 回测开始日期
        backtest_end_date: 回测结束日期
        strategy_keywords: 策略研究路由关键词列表
        stock_analysis_keywords: 股票分析路由关键词列表
    """

    llm_type: str = "ollama"
    llm_model: str = "deepseek-v4-flash:cloud"
    llm_base_url: str = "http://localhost:11434"
    memory_path: str = str(PROJECT_DATA_DIR / "memory.npz")
    init_dir: str = "./init"
    max_iterations: int = 3
    backtest_start_date: str = "2020-01-01"
    backtest_end_date: str = "2023-12-31"
    strategy_keywords: tuple[str, ...] = ("策略", "思路", "投资策略")
    stock_analysis_keywords: tuple[str, ...] = ("股票", "分析", "公司")

    @classmethod
    def from_env(cls) -> "AppConfig":
        """从环境变量创建配置实例

        Returns:
            AppConfig 实例
        """
        strategy_env = os.getenv("STRATEGY_KEYWORDS", "策略,思路,投资策略")
        stock_analysis_env = os.getenv("STOCK_ANALYSIS_KEYWORDS", "股票,分析,公司")

        return cls(
            llm_type=os.getenv("LLM_TYPE", "ollama"),
            llm_model=os.getenv("LLM_MODEL", "deepseek-v4-flash:cloud"),
            llm_base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434"),
            memory_path=os.getenv("MEMORY_PATH", str(PROJECT_DATA_DIR / "memory.npz")),
            init_dir=os.getenv("INIT_DIR", "./init"),
            max_iterations=int(os.getenv("MAX_ITERATIONS", "3")),
            backtest_start_date=os.getenv("BACKTEST_START_DATE", "2020-01-01"),
            backtest_end_date=os.getenv("BACKTEST_END_DATE", "2023-12-31"),
            strategy_keywords=tuple(
                k.strip() for k in strategy_env.split(",") if k.strip()
            ),
            stock_analysis_keywords=tuple(
                k.strip() for k in stock_analysis_env.split(",") if k.strip()
            ),
        )

    def validate(self) -> list[str]:
        """验证配置有效性

        Returns:
            错误消息列表，如果为空则表示配置有效
        """
        errors = []

        # 验证 LLM 类型
        if self.llm_type not in ["ollama", "dashscope", "openai"]:
            errors.append(f"无效的 LLM 类型：{self.llm_type}")

        # 验证迭代次数
        if self.max_iterations < 1:
            errors.append(f"最大迭代次数必须大于 0: {self.max_iterations}")

        return errors


def _resolve_env_file(env_file: str | Path | None, search_from: Path) -> Path | None:
    """解析最终要加载的 .env 文件路径。

    选择顺序：
    1. 显式传入的 ``env_file`` 优先（无论是否存在，原样返回；不存在由 ``load_dotenv``
       静默跳过）。
    2. ``LONG_EARN_ENV`` 已设置 → 寻找 ``.env.<name>``，存在则使用，不存在则回退默认。
    3. 默认 ``.env``。
    """
    if env_file is not None:
        return Path(env_file)

    env_name = os.environ.get(_ENV_NAME_VAR, "").strip()
    if env_name:
        candidate = search_from / f".env.{env_name}"
        if candidate.exists():
            return candidate
        _logger.info(
            f"{_ENV_NAME_VAR}={env_name} 但 {candidate} 不存在，回退默认 .env"
        )

    default_env = search_from / ".env"
    return default_env if default_env.exists() else None


def load_config(
    env_file: str | Path | None = None,
    search_from: Path | None = None,
    override: bool = False,
) -> AppConfig:
    """加载配置（dotenv + AppConfig.from_env 的统一入口）。

    这是项目中**唯一推荐**的配置入口，替代散落各处的 ``load_dotenv()`` + 手动
    ``AppConfig.from_env()`` 组合。详见 [ADR-007](docs/adr/007-config-centralization.md)。

    Args:
        env_file: 显式指定 .env 文件路径（优先级最高）。
        search_from: dotenv 查找的起点目录（默认为项目根 ``_project_root``）。
        override: 是否让 .env 文件覆盖已设的 os.environ（默认 False，符合
            "显式环境变量 > .env 文件 > 默认值" 的生产惯例）。

    Returns:
        AppConfig 实例

    多环境支持：
        export LONG_EARN_ENV=dev   → 加载 .env.dev
        export LONG_EARN_ENV=prod  → 加载 .env.prod
        不设置                       → 加载 .env
    """
    if search_from is None:
        search_from = _project_root

    resolved = _resolve_env_file(env_file, search_from)
    if resolved is not None and resolved.exists():
        load_dotenv(resolved, override=override)
        _logger.info(f"配置加载自 {resolved}（override={override}）")
    else:
        _logger.debug("无 .env 文件可加载，使用 os.environ + AppConfig 默认值")

    return AppConfig.from_env()
