"""服务层 — 核心抽象接口

所有服务均定义为 Protocol（结构化鸭子类型），便于测试 Mock 和实现替换。
遵循 Clean Architecture：本层定义抽象，infrastructure 层实现。
"""

from typing import Any, Protocol

# ── Memory Service (3-Tier Memory) ───────────────────────────────


class MemoryService(Protocol):
    """三级记忆系统 — 遵循 Letta/MemGPT 分级记忆模式

    Working: 会话级临时上下文（当前推理窗口）
    Core:    持久化事实、策略规则、用户偏好
    Archival: 历史经验、过往回测结果、已过期的规则
    """

    def recall(
        self,
        query: str,
        tier: str = "core",
        k: int = 3,
        **filters,
    ) -> list[dict[str, Any]]:
        """从指定记忆层级检索

        Args:
            query: 自然语言查询
            tier: 记忆层级 (working | core | archival)
            k: 返回结果数
            **filters: 元数据过滤 (category, term, source_file 等)

        Returns:
            [{content, metadata, similarity}, ...]
        """
        ...

    def remember(
        self,
        content: str,
        tier: str = "core",
        **metadata,
    ) -> str:
        """存入记忆

        Args:
            content: 文本内容
            tier: 目标层级
            **metadata: 元数据 (term, category, experience_type 等)

        Returns:
            事实 ID
        """
        ...

    def search(
        self,
        query: str,
        k: int = 3,
        **filters,
    ) -> list[str]:
        """便捷检索 — 返回格式化字符串结果

        Args:
            query: 自然语言查询
            k: 返回结果数
            **filters: 元数据过滤

        Returns:
            ["【来源: ...】\\n...", ...]
        """
        ...

    def reflect(
        self,
        session_summary: str,
    ) -> list[str]:
        """反思整合 — 将会话经验提炼为持久规则

        这是 Agent 反思循环的核心：每次策略研发完成后，
        将学到的教训提升到 Core，过期的规则归档到 Archival。

        Args:
            session_summary: 会话总结（设计思路、回测结果、反思结论）

        Returns:
            新创建/更新的事实 ID 列表
        """
        ...

    def relate(
        self,
        source: str,
        target: str,
        relation: str = "related_to",
        weight: float = 1.0,
    ) -> None:
        """建立知识实体间的关系边

        Args:
            source: 源实体 ID
            target: 目标实体 ID
            relation: 关系类型 (related_to | depends_on | contradicts | improves)
            weight: 关系强度 0-1
        """
        ...

    def initialize(self) -> None:
        """初始化记忆系统（加载持久化数据）"""
        ...


# ── LLM Service ──────────────────────────────────────────────────


class LLMService(Protocol):
    """LLM 调用服务"""

    def invoke(self, prompt: str, format: str = "") -> Any:
        """调用 LLM

        Args:
            prompt: 提示词
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
    ) -> dict[str, Any]:
        """运行回测

        Args:
            strategy_yaml: YAML DSL 策略描述
            start_date: 回测起始日期（覆盖策略中的默认值）
            end_date: 回测结束日期（覆盖策略中的默认值）

        Returns:
            回测结果字典。成功时包含 performance 指标；
            失败时包含 error, error_category, error_detail 字段。
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
