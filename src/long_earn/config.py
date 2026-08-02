"""应用上下文管理模块

参考 LangGraph Context 实践，提供统一的上下文管理，用于传递配置和依赖。
使用直接属性访问提供最佳类型安全支持。
"""

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from long_earn.core import storage as _storage
from long_earn.services import (
    BacktestService,
    LLMService,
    LoggerService,
    MemoryService,
    MonitoringService,
    StockService,
)

if TYPE_CHECKING:
    from long_earn.backtest.data.connector import DataConnector
    from long_earn.backtest.data.provider import MarketIntelligenceProvider
    from long_earn.backtest.data.realtime import RealtimeDataProvider
    from long_earn.ontology import Connector, OntologyRegistry
    from long_earn.operator_dev.backlog import OperatorBacklog

# 项目数据目录 — 统一由 core.storage 裁决（LONG_EARN_DATA_DIR → repo 同级 long-earn-data）
PROJECT_DATA_DIR = _storage.DEFAULT_DATA_DIR


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

    # 数据层（可选，ADR-014 阶段 F：DataConnector 替代 DataProvider）。
    # 字段名保留 data_provider 不破坏 dataclass 构造调用方，新增 data_connector
    # 属性作为新名字别名。新代码应用 data_connector / require_data_connector()。
    data_provider: "DataConnector | None" = None
    # 市场情报能力（可选，仅 ciccwm 可用时注入；与 data_provider 分离的第二组接口）
    market_intelligence: "MarketIntelligenceProvider | None" = None
    # 实时行情能力（可选，ADR-011 第三组；ADR-018：显式主/次源切换）
    realtime_provider: "RealtimeDataProvider | None" = None
    # 算子缺口队列（可选，gap_detector 写入 / operator_dev 消费）
    operator_backlog: "OperatorBacklog | None" = None
    # 本体论连接器（可选，ADR-014；上层通过概念取数，屏蔽多数据源细节）
    connector: "Connector | None" = None
    # 本体论注册表（可选，ADR-014；承载 OntologyGraph 供记忆激活图遍历用）
    ontology_registry: "OntologyRegistry | None" = None

    # ── 新名字别名属性 ─────────────────────────────────────────────────
    # ADR-014 阶段 F：data_provider 字段类型已升级为 DataConnector，新代码
    # 应使用 data_connector 属性名（指向同一字段）。

    @property
    def data_connector(self) -> "DataConnector | None":
        """数据连接器（ADR-014 阶段 F 新名字，等价于 ``self.data_provider``）。"""
        return self.data_provider

    @data_connector.setter
    def data_connector(self, value: "DataConnector | None") -> None:
        """数据连接器 setter（写入 data_provider 字段）。"""
        self.data_provider = value

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

    def require_data_connector(self) -> "DataConnector":
        """获取数据连接器，未注入时抛出明确错误。"""
        if self.data_provider is None:
            raise RuntimeError("DataConnector 未初始化")
        return self.data_provider

    def require_data_provider(self) -> "DataConnector":
        """[向后兼容] 等价于 :meth:`require_data_connector`。"""
        return self.require_data_connector()

    def require_market_intelligence(self) -> "MarketIntelligenceProvider":
        """获取市场情报提供者，未注入时抛出明确错误"""
        if self.market_intelligence is None:
            raise RuntimeError("MarketIntelligenceProvider 未初始化（ciccwm 不可用）")
        return self.market_intelligence

    def require_realtime(self) -> "RealtimeDataProvider":
        """获取实时行情提供者，未注入时抛出明确错误"""
        if self.realtime_provider is None:
            raise RuntimeError("RealtimeDataProvider 未初始化")
        return self.realtime_provider

    def prepare_context(
        self,
        query: str,
        *,
        k: int = 5,
        force_refresh: bool = False,
    ) -> str:
        """激活研究/分析上下文（ADR-018 基础设施）。

        1. 尝试 ``memory.activate_events``
        2. 若为空或 ``force_refresh``，用默认 Collector 跑轻量事件推理后再激活
        3. 返回可注入 prompt 的字符串（可能为空）
        """
        if not query.strip():
            return ""

        memory = self.memory
        activated: list[str] = []
        if hasattr(memory, "activate_events") and not force_refresh:
            try:
                raw = memory.activate_events(query, k=k)
                activated = [str(x) for x in (raw or [])]
            except Exception as exc:
                self.logger.warning(f"prepare_context activate 失败: {exc}")

        if activated and not force_refresh:
            return "\n".join(activated)

        # miss / 强制刷新 → 轻量事件推理
        try:
            from long_earn.event_inference import (  # noqa: PLC0415
                create_event_inference_subgraph,
            )
            from long_earn.event_inference.collectors import (  # noqa: PLC0415
                create_default_collector_registry,
            )

            registry = create_default_collector_registry(
                market_intelligence=self.market_intelligence,
            )
            subgraph = create_event_inference_subgraph(
                self,
                registry=registry,
            )
            subgraph.invoke({"query": query})
        except Exception as exc:
            self.logger.warning(f"prepare_context 事件推理跳过: {exc}")

        if hasattr(memory, "activate_events"):
            try:
                raw = memory.activate_events(query, k=k)
                activated = [str(x) for x in (raw or [])]
            except Exception as exc:
                self.logger.warning(f"prepare_context 二次激活失败: {exc}")
                return ""
        return "\n".join(activated)


@dataclass
class AppConfig:
    """应用配置（环境变量单一真相源）

    所有环境变量集中在此文档维护，AGENTS.md / README 不再重复。新增环境变量时
    同步更新此 docstring，并在 ``from_env`` 中读取。

    ── AppConfig.from_env() 读取的业务配置 ─────────────────────────────

    | 变量 | 默认值 | 说明 |
    |------|--------|------|
    | LLM_TYPE | deepseek | LLM 类型（deepseek / ollama / dashscope / openai） |
    | LLM_MODEL | deepseek-v4-flash | LLM 模型名称 |
    | LLM_BASE_URL | https://api.deepseek.com/v1 | LLM API 基础 URL |
    | LONG_EARN_DATA_DIR | D:/dev/long-earn-data | 统一数据根目录（唯一存储位置控制变量，派生全部生成数据路径） |
    | INIT_DIR | ./init | 知识库初始化目录 |
    | BACKTEST_START_DATE | 2020-01-01 | 回测默认起始日期 |
    | BACKTEST_END_DATE | 2023-12-31 | 回测默认结束日期 |
    | TRAIN_START | 2022-01-01 | 训练集起始（量化数据分割规范） |
    | TRAIN_END | 2024-12-31 | 训练集结束 |
    | TEST_START | 2025-01-01 | 测试集起始（Walk-Forward OOS） |
    | TEST_END | 2026-03-24 | 测试集结束 |
    | VALIDATION_START | 2026-03-25 | 验证集起始（前瞻验证） |
    | VALIDATION_END | 2026-06-25 | 验证集结束 |
    | MAX_ITERATIONS | 3 | 策略研发最大迭代次数 |
    | HTR_MAX_SELECT | 1 | HTR 每轮选择的最大假设数（1=串行，>1 激活 LangGraph Send 并行 fan-out） |
    | HTR_MAX_CYCLES | 10 | HTR 六步循环最大周期数（达到时强制停止） |
    | LONG_EARN_MAX_WORKERS | 0 | 回测并行 worker 数（0=自动 cpu_count，1=串行，>1=指定核数） |
    | STRATEGY_KEYWORDS | 策略,思路,投资策略 | 策略研究路由关键词（逗号分隔） |
    | STOCK_ANALYSIS_KEYWORDS | 股票,分析,公司 | 股票分析路由关键词（逗号分隔） |
    | EVENT_INFERENCE_KEYWORDS | 新闻,事件,热点,资讯,利好,利空 | 事件推理路由关键词（逗号分隔） |

    ── 运行时控制环境变量（不在 from_env，由各模块直接读取） ──────────

    | 变量 | 读取位置 | 说明 |
    |------|----------|------|
    | LONG_EARN_SKIP_CACHE_SYNC | context_init.py | =1 跳过启动时批量增量同步（CI/加速启动；读路径仍可按需从 miniqmt 补洞） |
    | LONG_EARN_CACHE_ONLY | cache_sync.py / miniqmt_provider.py | =1 显式强制纯缓存（禁止按需拉 miniqmt；默认不在启动同步后自动设置） |
    | LONG_EARN_DISABLE_XTQUANT | parallel.py / miniqmt_provider.py | =1 禁用 xtquant（CI/无 QMT；并行 worker 内临时设置，避免 C++ 崩溃） |

    ── 第三方 API Key 环境变量 ────────────────────────────────────────

    | 变量 | 读取位置 | 说明 |
    |------|----------|------|
    | DEEPSEEK_API_KEY | utils/llm_factory.py | DeepSeek API Key（LLM_TYPE=deepseek 时必填） |
    | DASHSCOPE_API_KEY | utils/llm_factory.py | 阿里百炼 API Key（LLM_TYPE=dashscope 时必填） |
    | OPENAI_API_KEY | langchain_openai（隐式读取） | OpenAI API Key（LLM_TYPE=openai 时必填） |
    | MOONSHOT_API_KEY / KIMI_API_KEY | event_inference/collectors/kimi_collector.py / tools/kimi_web_search.py | Kimi / Moonshot API Key（事件推理采集 + 网页搜索，二选一） |

    Attributes:
        llm_type: LLM 类型，可选值：deepseek, ollama, dashscope, openai
        llm_model: LLM 模型名称
        llm_base_url: LLM API 基础 URL
        data_dir: 统一数据根目录（LONG_EARN_DATA_DIR → repo 同级 long-earn-data）
        memory_path: 记忆持久化路径（DuckDB）
        backtest_cache_path: 回测缓存 DuckDB 路径
        hypothesis_tree_dir: 假设树 JSON 存储目录
        init_dir: 知识库初始化目录
        max_iterations: 最大迭代次数
        backtest_start_date: 回测开始日期
        backtest_end_date: 回测结束日期
        strategy_keywords: 策略研究路由关键词列表
        stock_analysis_keywords: 股票分析路由关键词列表
    """

    llm_type: str = "deepseek"
    llm_model: str = "deepseek-v4-flash"
    llm_base_url: str = "https://api.deepseek.com/v1"
    # 统一数据根目录（LONG_EARN_DATA_DIR → repo 同级 long-earn-data）
    data_dir: str = str(_storage.DEFAULT_DATA_DIR)
    # 记忆持久化路径（由 data_dir 派生）
    memory_path: str = str(_storage.substances_db_path())
    # 回测缓存 DuckDB 路径（由 data_dir 派生）
    backtest_cache_path: str = str(_storage.backtest_cache_path())
    # 假设树存储目录（由 data_dir 派生，ADR-010 HTR）
    hypothesis_tree_dir: str = str(_storage.hypothesis_tree_dir())
    # 策略研发产物路径（由 data_dir 派生）
    strategy_results_path: str = str(_storage.strategy_results_path())
    best_strategy_path: str = str(_storage.best_strategy_path())
    init_dir: str = "./init"
    max_iterations: int = 3
    # HTR 每轮选择的最大假设数（1=串行，>1=并行 fan-out，ADR-010 Phase 5）
    htr_max_select: int = 1
    # HTR 六步循环最大周期数（_decide_node 强制停止兜底，ADR-010）
    # 默认 10 与原硬编码一致；可通过 HTR_MAX_CYCLES 环境变量配置
    htr_max_cycles: int = 10
    # 回测并行 worker 数（0=自动使用 os.cpu_count()，1=串行，>1=指定核数）
    # 控制 ParallelRunner / Walk-Forward fold 级并行的并发度
    max_workers: int = 0
    backtest_start_date: str = "2020-01-01"
    backtest_end_date: str = "2023-12-31"
    # 量化数据分割（AGENTS.md「量化数据分割规范」）
    train_start_date: str = "2022-01-01"
    train_end_date: str = "2024-12-31"
    test_start_date: str = "2025-01-01"
    test_end_date: str = "2026-03-24"
    validation_start_date: str = "2026-03-25"
    validation_end_date: str = "2026-06-25"
    strategy_keywords: tuple[str, ...] = ("策略", "思路", "投资策略")
    stock_analysis_keywords: tuple[str, ...] = ("股票", "分析", "公司")
    event_inference_keywords: tuple[str, ...] = (
        "新闻",
        "事件",
        "热点",
        "资讯",
        "利好",
        "利空",
    )

    @classmethod
    def from_env(cls) -> "AppConfig":
        """从环境变量创建配置实例

        Returns:
            AppConfig 实例
        """
        strategy_env = os.getenv("STRATEGY_KEYWORDS", "策略,思路,投资策略")
        stock_analysis_env = os.getenv("STOCK_ANALYSIS_KEYWORDS", "股票,分析,公司")
        event_env = os.getenv(
            "EVENT_INFERENCE_KEYWORDS", "新闻,事件,热点,资讯,利好,利空"
        )

        # 唯一存储环境变量：LONG_EARN_DATA_DIR → 派生全部数据路径
        paths = _storage.resolve_paths(os.getenv("LONG_EARN_DATA_DIR"))

        return cls(
            llm_type=os.getenv("LLM_TYPE", "deepseek"),
            llm_model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
            data_dir=str(paths["data_dir"]),
            memory_path=str(paths["substances_db_path"]),
            backtest_cache_path=str(paths["backtest_cache_path"]),
            hypothesis_tree_dir=str(paths["hypothesis_tree_dir"]),
            strategy_results_path=str(paths["strategy_results_path"]),
            best_strategy_path=str(paths["best_strategy_path"]),
            init_dir=os.getenv("INIT_DIR", "./init"),
            max_iterations=int(os.getenv("MAX_ITERATIONS", "3")),
            htr_max_select=int(os.getenv("HTR_MAX_SELECT", "1")),
            htr_max_cycles=int(os.getenv("HTR_MAX_CYCLES", "10")),
            max_workers=int(os.getenv("LONG_EARN_MAX_WORKERS", "0")),
            backtest_start_date=os.getenv("BACKTEST_START_DATE", "2020-01-01"),
            backtest_end_date=os.getenv("BACKTEST_END_DATE", "2023-12-31"),
            train_start_date=os.getenv("TRAIN_START", "2022-01-01"),
            train_end_date=os.getenv("TRAIN_END", "2024-12-31"),
            test_start_date=os.getenv("TEST_START", "2025-01-01"),
            test_end_date=os.getenv("TEST_END", "2026-03-24"),
            validation_start_date=os.getenv("VALIDATION_START", "2026-03-25"),
            validation_end_date=os.getenv("VALIDATION_END", "2026-06-25"),
            strategy_keywords=tuple(
                k.strip() for k in strategy_env.split(",") if k.strip()
            ),
            stock_analysis_keywords=tuple(
                k.strip() for k in stock_analysis_env.split(",") if k.strip()
            ),
            event_inference_keywords=tuple(
                k.strip() for k in event_env.split(",") if k.strip()
            ),
        )

    def validate(self) -> list[str]:
        """验证配置有效性

        Returns:
            错误消息列表，如果为空则表示配置有效
        """
        errors = []

        # 验证 LLM 类型
        if self.llm_type not in ["deepseek", "ollama", "dashscope", "openai"]:
            errors.append(f"无效的 LLM 类型：{self.llm_type}")

        # 验证迭代次数
        if self.max_iterations < 1:
            errors.append(f"最大迭代次数必须大于 0: {self.max_iterations}")

        return errors
