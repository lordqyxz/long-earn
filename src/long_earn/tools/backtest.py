import importlib.util
import os
import tempfile
from pathlib import Path

from importlib.machinery import ModuleSpec
from types import ModuleType
from typing import Optional, Union, Dict

import numpy as np
import pandas as pd

# 导入 qlib
try:
    from qlib.data import D
    from qlib import init

    # 初始化 qlib，使用用户数据文件夹
    qlib_data_path = Path.home() / ".qlib_data"
    if qlib_data_path.exists():
        init(provider_uri=str(qlib_data_path), region="cn")
    QLIB_AVAILABLE = True
except Exception as e:
    print(f"警告：qlib 初始化失败，将使用模拟数据：{e}")
    QLIB_AVAILABLE = False


def run_backtest(
    strategy_path: str = "",
    strategy_code: str = "",
    start_date: str = "2020-01-01",
    end_date: str = "2023-12-31",
) -> Optional[Dict]:
    """
    回测交易策略

    Args:
        strategy_path: 策略文件路径（与 strategy_code 二选一）
        strategy_code: 策略代码字符串（与 strategy_path 二选一）
        start_date: 回测开始日期
        end_date: 回测结束日期

    Returns:
        dict: 回测结果字典，包含总收益率、夏普比率、最大回撤等指标
    """
    try:
        if strategy_code:
            strategy_name = "dynamic_strategy"
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(strategy_code)
                temp_path = f.name

            try:
                spec: Optional[ModuleSpec] = importlib.util.spec_from_file_location(
                    strategy_name, temp_path
                )
                if spec is None:
                    print("无法从文件创建模块规范")
                    return None
                strategy_module: ModuleType = importlib.util.module_from_spec(spec)
                if spec.loader is not None:
                    spec.loader.exec_module(strategy_module)
            finally:
                os.unlink(temp_path)
        else:
            strategy_name = os.path.splitext(os.path.basename(strategy_path))[0]
            spec: Optional[ModuleSpec] = importlib.util.spec_from_file_location(
                strategy_name, strategy_path
            )
            if spec is None:
                print("无法从文件创建模块规范")
                return None
            strategy_module: ModuleType = importlib.util.module_from_spec(spec)
            if spec.loader is not None:
                spec.loader.exec_module(strategy_module)
    except FileNotFoundError:
        print(f"策略文件未找到：{strategy_path}")
        return None
    except Exception as e:
        print(f"加载策略文件失败：{e}")
        return None

    strategy_class = None
    for name, obj in strategy_module.__dict__.items():
        if hasattr(obj, "generate_signals"):
            strategy_class = obj
            break

    if not strategy_class:
        print("策略类未找到")
        return None

    try:
        strategy = strategy_class()
    except Exception as e:
        print(f"创建策略实例失败：{e}")
        return None

    # 获取交易日历
    if QLIB_AVAILABLE:
        try:
            from qlib.data import D as qlib_D  # 本地导入避免 linter 错误

            dates = qlib_D.calendar(
                start_time=start_date, end_time=end_date, freq="day"
            )
            dates = pd.to_datetime(dates)
        except Exception as e:
            print(f"获取交易日历失败：{e}，使用工作日代替")
            dates = pd.date_range(start=start_date, end=end_date, freq="B")
    else:
        dates = pd.date_range(start=start_date, end=end_date, freq="B")

    daily_returns = []

    for date in dates:
        date_str = date.strftime("%Y-%m-%d")
        try:
            signals = strategy.generate_signals(date_str)
        except Exception as e:
            print(f"生成信号失败 ({date_str}): {e}")
            continue

        if signals is None or len(signals) == 0:
            continue

        try:
            # 使用 qlib 获取真实收益率
            if QLIB_AVAILABLE:
                portfolio_return = _get_portfolio_return_qlib(signals, date_str)
            else:
                # qlib 不可用时使用模拟数据（仅用于测试）
                portfolio_return = _get_portfolio_return_mock(signals, date_str)

            if portfolio_return is not None:
                daily_returns.append(portfolio_return)
        except Exception as e:
            print(f"计算组合收益失败 ({date_str}): {e}")
            continue

    if not daily_returns:
        print("没有有效的回测数据")
        return None

    returns_series = pd.Series(daily_returns)

    total_return = (1 + returns_series).cumprod().iloc[-1] - 1
    sharpe_ratio = (
        returns_series.mean() / returns_series.std() * np.sqrt(252)
        if returns_series.std() != 0
        else 0
    )
    cumulative = (1 + returns_series).cumprod()
    max_drawdown = (cumulative.cummax() - cumulative).max() / cumulative.cummax().max()

    print(f"回测期间：{start_date} 至 {end_date}")
    print(f"交易日数量：{len(daily_returns)}")
    print(f"总收益率：{total_return:.2%}")
    print(f"年化收益率：{(1 + total_return) ** (252 / len(daily_returns)) - 1:.2%}")
    print(f"夏普比率：{sharpe_ratio:.2f}")
    print(f"最大回撤：{max_drawdown:.2%}")

    return {
        "total_return": float(total_return),
        "annual_return": (
            float((1 + total_return) ** (252 / len(daily_returns)) - 1)
            if daily_returns
            else 0.0
        ),
        "sharpe_ratio": float(sharpe_ratio),
        "max_drawdown": float(max_drawdown),
        "win_rate": (
            float((returns_series > 0).sum() / len(returns_series))
            if len(returns_series) > 0
            else 0.0
        ),
        "trading_days": len(daily_returns),
    }


def _get_portfolio_return_qlib(signals: Union[Dict, pd.Series], date_str: str) -> float:
    """
    使用 qlib 获取真实市场收益率

    Args:
        signals: 交易信号，key 为股票代码，value 为仓位权重
        date_str: 交易日期字符串

    Returns:
        float: 组合收益率
    """
    # 检查 signals 是否为空
    if signals is None:
        return 0.0
    if hasattr(signals, "__len__") and len(signals) == 0:
        return 0.0

    # 获取所有股票的收盘价
    stock_list = list(signals.keys())
    if len(stock_list) == 0:
        return 0.0

    try:
        # 获取当日和前一个交易日的收盘价
        end_date = pd.Timestamp(date_str)
        start_date = end_date - pd.Timedelta(days=10)  # 多获取几天确保有数据

        from qlib.data import D as qlib_D  # 本地导入避免 linter 错误

        close_data = qlib_D.features(
            stock_list, ["$close"], start_time=start_date, end_time=end_date
        )

        # 计算每只股票的收益率
        portfolio_return = 0.0
        total_weight = 0.0

        for stock, weight in signals.items():
            if weight == 0:
                continue

            if stock in close_data.columns:
                stock_close = close_data[stock]["$close"]
                if len(stock_close) >= 2:
                    # 计算当日收益率
                    latest_close = stock_close.iloc[-1]
                    prev_close = stock_close.iloc[-2]

                    if prev_close > 0:
                        stock_return = (latest_close - prev_close) / prev_close
                        portfolio_return += weight * stock_return
                        total_weight += abs(weight)

        # 归一化
        if total_weight > 0:
            portfolio_return /= total_weight

        return portfolio_return

    except Exception as e:
        print(f"获取 qlib 数据失败：{e}")
        return 0.0


def _get_portfolio_return_mock(signals: Union[Dict, pd.Series], date_str: str) -> float:
    """
    模拟收益率（仅在 qlib 不可用时使用）

    Args:
        signals: 交易信号
        date_str: 交易日期字符串（用于生成确定性随机数）

    Returns:
        float: 模拟组合收益率
    """
    # 使用日期作为随机种子，确保同一日期的结果一致
    date_hash = hash(date_str) % (2**32)
    np.random.seed(int(date_hash))

    stock_returns = {stock: np.random.normal(0, 0.02) for stock in signals.keys()}

    portfolio_return = sum(
        signals[stock] * stock_returns.get(stock, 0) for stock in signals.keys()
    ) / max(len(signals), 1)

    return portfolio_return
