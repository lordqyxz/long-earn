"""应用上下文管理模块

参考 LangGraph Context 实践，提供统一的上下文管理，用于传递配置和依赖。
使用直接属性访问提供最佳类型安全支持。
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

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

# 项目数据目录
_project_root = Path(__file__).parent.parent.parent.parent
PROJECT_DATA_DIR = _project_root / ".data"


@dataclass
class RuntimeContext:
    """运行时上下文实现

    参考 LangGraph 的 context 设计模式：
    1. 集中管理配置和依赖
    2. 直接属性访问
    3. 类型安全

    用法:
        context = RuntimeContext(
            config=AppConfig(),
            llm_service=LLMServiceImpl(context),
            memory=MemoryServiceImpl(context),
        )

        # 在节点或工具中使用（直接属性访问）
        response = context.llm_service.invoke(prompt)
        facts = context.memory.recall(query)

        # 使用提示词模板
        from long_earn.core.prompt_loader import MarkdownPromptTemplate
        prompt_template = MarkdownPromptTemplate("my_prompt.md", caller_file=__file__)
        prompt = prompt_template.format(query=query)
    """

    # 核心服务（初始化阶段可能为 None）
    llm_service: LLMService | None = None
    memory: MemoryService | None = None
    stock_service: StockService | None = None
    backtest_service: BacktestService | None = None

    # 基础设施
    logger: LoggerService | None = None
    monitoring: MonitoringService | None = None
    config: "AppConfig | None" = None

    # 数据层（可选）
    data_provider: "DataProvider | None" = None


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
