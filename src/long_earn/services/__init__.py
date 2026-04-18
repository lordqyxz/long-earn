"""服务层模块

提供核心服务的抽象接口和实现。
"""

from collections.abc import Callable
from typing import Any, Protocol


class LLMService(Protocol):
    """LLM 服务接口"""

    def invoke(self, prompt: str) -> Any:
        """调用 LLM

        Args:
            prompt: 提示词

        Returns:
            LLM 响应
        """
        ...

    def get_llm(self) -> Any:
        """获取底层 LLM 实例

        Returns:
            LLM 实例
        """
        ...


class KnowledgeService(Protocol):
    """知识存储服务接口"""

    def search(self, query: str, k: int = 3, **kwargs) -> list[str]:
        """搜索知识

        Args:
            query: 搜索查询
            k: 返回结果数量
            **kwargs: 额外参数

        Returns:
            搜索结果列表
        """
        ...

    def save(self, content: str, metadata: dict[str, Any]) -> bool:
        """保存知识

        Args:
            content: 内容
            metadata: 元数据

        Returns:
            是否保存成功
        """
        ...

    def initialize(self) -> None:
        """初始化知识库"""
        ...


class StockService(Protocol):
    """股票数据服务接口"""

    def get_stock_data(self, stock_code: str) -> dict[str, Any]:
        """获取股票数据

        Args:
            stock_code: 股票代码

        Returns:
            股票数据字典
        """
        ...

    def get_financial_metrics(
        self, stock_code: str, start_year: str = "2021"
    ) -> dict[str, Any]:
        """获取财务指标

        Args:
            stock_code: 股票代码
            start_year: 起始年份

        Returns:
            财务指标字典
        """
        ...

    def get_price_history(self, stock_code: str) -> list:
        """获取价格历史

        Args:
            stock_code: 股票代码

        Returns:
            价格历史列表
        """
        ...

    def get_stock_code_by_name(self, stock_name: str) -> str:
        """根据股票名称获取代码

        Args:
            stock_name: 股票名称

        Returns:
            股票代码
        """
        ...


class BacktestService(Protocol):
    """回测服务接口"""

    def run_backtest(
        self,
        strategy_code: str,
        start_date: str = "2020-01-01",
        end_date: str = "2023-12-31",
    ) -> dict[str, Any] | None:
        """运行回测

        Args:
            strategy_code: 策略代码
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            回测结果字典
        """
        ...


class LoggerService(Protocol):
    """日志服务接口"""

    def debug(self, message: str) -> None:
        """调试日志"""
        ...

    def info(self, message: str) -> None:
        """信息日志"""
        ...

    def warning(self, message: str) -> None:
        """警告日志"""
        ...

    def error(self, message: str) -> None:
        """错误日志"""
        ...

    def exception(self, message: str) -> None:
        """异常日志"""
        ...


class MonitoringService(Protocol):
    """监控服务接口"""

    def track(self, node_name: str) -> Any:
        """创建监控上下文管理器"""
        ...

    def monitor_node(self, node_name: str) -> Callable:
        """节点监控装饰器"""
        ...

    def monitor_prompt(self, prompt_name: str) -> Callable:
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
