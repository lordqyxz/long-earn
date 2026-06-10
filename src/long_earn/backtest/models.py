"""回测引擎数据模型"""

from typing import Any

from pydantic import BaseModel, Field


class BacktestResult(BaseModel):
    """回测结果模型"""

    success: bool = Field(default=True, description="是否成功")
    message: str = Field(default="回测成功", description="结果信息")
    error_category: str | None = Field(default=None, description="错误分类")
    error_detail: str | None = Field(default=None, description="详细错误")

    # 绩效指标
    total_return: float | None = Field(default=None, description="总收益率")
    annual_return: float | None = Field(default=None, description="年化收益率")
    sharpe_ratio: float | None = Field(default=None, description="夏普比率")
    max_drawdown: float | None = Field(default=None, description="最大回撤")
    win_rate: float | None = Field(default=None, description="胜率")
    trading_days: int | None = Field(default=None, description="交易天数")
    volatility: float | None = Field(default=None, description="波动率")
    calmar_ratio: float | None = Field(default=None, description="卡玛比率")
    sortino_ratio: float | None = Field(default=None, description="索提诺比率")

    # 基准对比指标
    alpha: float | None = Field(default=None, description="Alpha 超额收益")
    beta: float | None = Field(default=None, description="Beta 市场敏感度")
    information_ratio: float | None = Field(default=None, description="信息比率")
    tracking_error: float | None = Field(default=None, description="跟踪误差")
    benchmark_return: float | None = Field(default=None, description="基准收益率")

    # 详细数据
    daily_returns: list[dict[str, Any]] | None = Field(
        default=None, description="每日收益率序列"
    )
    positions_history: list[dict[str, Any]] | None = Field(
        default=None, description="每日持仓权重历史"
    )
    trade_count: int | None = Field(default=None, description="总交易次数")
    attribution: dict[str, float] | None = Field(
        default=None, description="每只股票的 P&L 归因"
    )
