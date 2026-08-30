"""服务层 — 核心抽象接口

所有服务均定义为 Protocol（结构化鸭子类型），便于测试 Mock 和实现替换。
遵循 Clean Architecture：本层定义抽象，infrastructure 层实现。
"""

from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import BaseMessage

from long_earn.backtest import ParamGrid

# ── Memory Service ───────────────────────────────────────────────


@dataclass(frozen=True)
class StrategyExperience:
    """策略经验值对象 — 统一 save/search 数据契约，消灭 markdown 往返 regex。"""

    name: str
    code: str
    rationale: str
    metrics: dict[str, Any]
    reflection: str = ""
    error_history: list[dict[str, Any]] | None = None


class MemoryService(Protocol):
    """记忆服务 — 知识与策略经验的统一存取（ADR-007 Substance 后端）。

    4 方法接口（ADR-007 破坏性收窄，删僵尸方法 reflect/relate/remember/recall
    + tier 死参）。
    """

    def search(self, query: str, k: int = 3, **filters: Any) -> list[str]:
        """检索知识/经验片段，返回可注入 prompt 的格式化字符串。

        Args:
            query: 自然语言查询
            k: 返回结果数
            **filters: 元数据过滤 (category, term, source_file 等)

        Returns:
            ["【来源: ...】\\n...", ...]
        """
        ...

    def save_experience(self, experience: StrategyExperience) -> str:
        """保存一次策略研发经验，返回经验 ID。

        Args:
            experience: 策略经验值对象

        Returns:
            经验 ID（Substance sid）
        """
        ...

    def search_experience(
        self,
        query: str,
        k: int = 3,
        min_sharpe: float | None = None,
        required_outcome: str | None = None,
    ) -> list[StrategyExperience]:
        """按语义检索同类历史策略经验。

        Args:
            query: 查询文本
            k: 返回结果数
            min_sharpe: 最低夏普比率过滤（None 表示不过滤）
            required_outcome: 若给定，只返回 ``metrics.outcome == required_outcome``
                的经验（大小写不敏感）

        Returns:
            匹配的策略经验列表
        """
        ...

    def initialize(self) -> None:
        """初始化记忆系统（加载持久化数据 / init 目录）。"""
        ...

    def save_hypothesis_tree(
        self,
        run_id: str,
        best_insight: str,
        best_direction: str,
        node_count: int,
    ) -> str:
        """保存假设树摘要到记忆（ADR-010 Phase 4 hot-start）。

        Args:
            run_id: 研究 run ID
            best_insight: 最佳洞察摘要
            best_direction: 最佳改进方向
            node_count: 节点总数

        Returns:
            物质 ID
        """
        ...

    def search_hypothesis_trees(
        self,
        query: str,
        k: int = 3,
    ) -> list[dict[str, Any]]:
        """检索历史假设树摘要（ADR-010 Phase 4 hot-start）。

        Args:
            query: 查询文本
            k: 返回结果数

        Returns:
            匹配的树摘要列表，每项含 run_id / best_insight / best_direction
        """
        ...

    def save_events(
        self,
        events: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        conflict_groups: dict[int, str] | None = None,
        collected_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """保存采集原文、候选断言与影响关系（ADR-023）。

        采集原文以 ``review_status=raw`` 落库，内容不可覆盖；抽取事件为
        ``staging`` Claim；同标的相反情绪写 ``contradicts`` 边并保留双方。

        Args:
            events: 事件 dict 列表
            relations: 关系 dict 列表
            conflict_groups: 事件下标 → 冲突组 ID
            collected_items: 原始素材 dict（title/content/url/source/published_at）

        Returns:
            event_sids / relation_sids / raw_sids / event_count / relation_count
        """
        ...

    def activate_events(
        self,
        query: str,
        k: int = 5,
        include_relations: bool = True,
        include_staging: bool = False,
    ) -> list[str]:
        """WorldInfo 激活引擎 — 关键词触发事件/关系物质（ADR-007 Phase 3）。

        默认只注入 ``committed``；``include_staging=True`` 才纳入未过门断言。
        RAW 证据永不进入激活列表。

        Args:
            query: 触发文本
            k: 返回物质数上限
            include_relations: 是否同时返回 RELATION
            include_staging: 是否注入暂存断言

        Returns:
            可直接注入 prompt 的格式化字符串列表
        """
        ...


# ── Context Preparation Service ──────────────────────────────────


@dataclass(frozen=True)
class ContextActivation:
    """上下文激活结果 — 确定性脚手架的结构化产物（ADR-021）。

    脚手架层不内嵌 LLM 推理；未命中（``missed``）时的采集推理由
    调用方在 agent 层显式触发。
    """

    items: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        """可直接注入 prompt 的激活文本。"""
        return "\n".join(self.items)

    @property
    def missed(self) -> bool:
        """是否未命中任何事件/知识。"""
        return not self.items


class ContextPreparationService(Protocol):
    """研究与分析入口的上下文准备服务（纯确定性激活，ADR-021）。"""

    def prepare(
        self,
        query: str,
        *,
        k: int = 5,
    ) -> ContextActivation:
        """激活与查询相关的事件/知识，不触发任何采集推理。"""
        ...


class LLMService(Protocol):
    """LLM 调用服务"""

    def invoke(self, prompt: str | list[BaseMessage], format: str = "") -> Any:
        """调用 LLM

        Args:
            prompt: 提示词，可为字符串或 BaseMessage 列表（多消息对话）。
                LangChain llm.invoke() 原生支持这两种入参类型。
            format: 输出格式，可选 "json" 强制 JSON 输出

        Returns:
            LLM 响应
        """
        ...

    def get_llm(self) -> Any:
        """获取底层 LLM 实例"""
        ...


# ── Backtest Service ─────────────────────────────────────────────


class BacktestService(Protocol):
    """回测服务 — 执行 YAML DSL 策略回测"""

    def run(
        self,
        strategy_yaml: str,
        start_date: str = "",
        end_date: str = "",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """运行回测

        Args:
            strategy_yaml: YAML DSL 策略描述
            start_date: 回测起始日期（覆盖策略中的默认值）
            end_date: 回测结束日期（覆盖策略中的默认值）
            tags: run 级标签，写入 RUN_START payload.tags；未显式指定时引擎
                按策略 DSL ``kind`` 自动打标（production→prod 清理豁免，
                research→test 可清理）。

        Returns:
            回测结果字典。成功时包含 performance 指标；
            失败时包含 error, error_category, error_detail 字段。
        """
        ...

    def run_oos(
        self,
        strategy_yaml: str,
        start_date: str = "",
        end_date: str = "",
        n_splits: int = 3,
        gap: int = 5,
    ) -> dict[str, Any]:
        """运行 Walk-Forward OOS 验证（ADR-010 held-out 门）

        Args:
            strategy_yaml: YAML DSL 策略描述
            start_date: OOS 区间起始（测试集）
            end_date: OOS 区间结束
            n_splits: Walk-Forward 折叠数
            gap: train/test 间隔离交易日数（默认 5）

        Returns:
            Walk-Forward 结果字典，含 oos_sharpe / fold_results / average_test_metrics
        """
        ...

    def run_candidates(
        self,
        strategy_yamls: list[str],
        start_date: str = "",
        end_date: str = "",
        universe_type: str = "",
        tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """批量并行回测多个候选策略（ADR-010 阶段 5 收尾）

        共享数据面板 + 进程池分发，各候选独立解析风控参数与 warmup
        （ADR-008 B5），diagnostics 保真回传（ADR-008 B6）。

        Args:
            strategy_yamls: 各候选的完整策略 YAML 列表
            start_date: 回测起始日期（默认 config.backtest_start_date，训练集）
            end_date: 回测结束日期（默认 config.backtest_end_date，训练集）
            universe_type: 股票池类型；缺省从首候选 DSL 解析
            tags: run 级标签，透传各候选 RUN_START payload.tags；未显式指定时
                引擎按各候选 DSL ``kind`` 自动打标。

        Returns:
            与 strategy_yamls 等长的结果列表，每项与 run() 返回结构一致。
        """
        ...

    def run_grid(  # noqa: PLR0913
        self,
        strategy_template: str,
        param_grid: ParamGrid,
        start_date: str = "",
        end_date: str = "",
        universe_type: str = "main_board+gem",
        benchmark_symbol: str = "",
        allow_large_grid: bool = False,
    ) -> dict[str, Any]:
        """参数网格寻优：在训练集上并行暴力搜索最优参数组合。

        对策略模板的 ``{{ var }}`` 占位符做笛卡尔积展开，进程池并行回测，
        返回 Top-K 最优结果（引擎内参数组合上限 ``_MAX_GRID_DEFAULT``）。

        Args:
            strategy_template: 策略 YAML 模板，使用 {{ var }} 作为参数占位符
            param_grid: 参数网格（标量 scalars + 结构化 structs，笛卡尔积展开）
            start_date: 回测起始日期（默认 config.backtest_start_date，训练集）
            end_date: 回测结束日期（默认 config.backtest_end_date，训练集）
            universe_type: 股票池类型（默认 main_board+gem）
            benchmark_symbol: 基准标的代码（默认空串表示无基准）
            allow_large_grid: 允许超过引擎参数组合上限（默认 False）

        Returns:
            网格寻优结果字典，含 total / success_count / failure_count /
            best_sharpe / best_return / best_param_desc / outcomes（Top-K）。
        """
        ...


# ── Stock Service ────────────────────────────────────────────────


class StockService(Protocol):
    """股票数据查询服务"""

    def get_stock_data(self, stock_code: str) -> dict[str, Any]:
        """获取股票实时数据（行情 + 基本信息）"""
        ...

    def get_financial_metrics(
        self, stock_code: str, start_year: str = "2021"
    ) -> dict[str, Any]:
        """获取财务指标（ROE, EPS, 营收增长率等）"""
        ...

    def get_price_history(self, stock_code: str) -> list:
        """获取历史价格序列"""
        ...

    def get_stock_code_by_name(self, stock_name: str) -> str:
        """按股票名称查询代码"""
        ...


# ── Observability ────────────────────────────────────────────────


class LoggerService(Protocol):
    """日志服务"""

    def debug(self, message: str) -> None: ...
    def info(self, message: str) -> None: ...
    def warning(self, message: str) -> None: ...
    def error(self, message: str) -> None: ...
    def exception(self, message: str) -> None: ...


class MonitoringService(Protocol):
    """监控服务 — 性能追踪和 Token 统计"""

    def track(self, node_name: str) -> Any:
        """创建监控上下文管理器"""
        ...

    def monitor_node(self, node_name: str) -> Any:
        """节点监控装饰器"""
        ...

    def monitor_prompt(self, prompt_name: str) -> Any:
        """提示词监控装饰器"""
        ...

    def track_tokens(self, usage_metadata: dict[str, Any]) -> None:
        """追踪 token 使用"""
        ...

    def get_metrics(self, name: str) -> Any:
        """获取性能指标"""
        ...

    def log_report(self, logger: LoggerService) -> None:
        """输出性能报告"""
        ...
