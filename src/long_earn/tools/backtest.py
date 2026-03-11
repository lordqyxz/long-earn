import importlib.util
import os
from types import ModuleType
from importlib.machinery import ModuleSpec
from typing import Optional
import pandas as pd
import numpy as np
from langchain.tools import tool

@tool
def run_backtest(strategy_path: str, start_date: str = "2020-01-01", end_date: str = "2023-12-31") -> dict | None:
    """
    回测交易策略
    
    Args:
        strategy_path: 策略文件路径
        start_date: 回测开始日期
        end_date: 回测结束日期
    
    Returns:
        dict: 回测结果字典，包含总收益率、夏普比率、最大回撤等指标
    """
    try:
        strategy_name = os.path.splitext(os.path.basename(strategy_path))[0]
        spec: Optional[ModuleSpec] = importlib.util.spec_from_file_location(strategy_name, strategy_path)
        if spec is None:
            print("无法从文件创建模块规范")
            return None
        strategy_module: ModuleType = importlib.util.module_from_spec(spec)
        if spec.loader is not None:
            spec.loader.exec_module(strategy_module)
    except FileNotFoundError:
        print(f"策略文件未找到: {strategy_path}")
        return None
    except Exception as e:
        print(f"加载策略文件失败: {e}")
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
        print(f"创建策略实例失败: {e}")
        return None
    
    dates = pd.date_range(start=start_date, end=end_date, freq="B")
    
    daily_returns = []
    
    for date in dates:
        date_str = date.strftime("%Y-%m-%d")
        try:
            signals = strategy.generate_signals(date_str)
        except Exception as e:
            print(f"生成信号失败: {e}")
            continue
        
        if signals is None:
            continue
        
        try:
            stock_returns = {stock: np.random.normal(0, 0.02) for stock in signals.keys()}
            
            portfolio_return = sum(
                signals[stock] * stock_returns.get(stock, 0) 
                for stock in signals.keys()
            ) / len(signals) if signals else 0
            
            daily_returns.append(portfolio_return)
        except Exception as e:
            print(f"计算组合收益失败: {e}")
            continue
    
    if not daily_returns:
        print("没有有效的回测数据")
        return None
    
    returns_series = pd.Series(daily_returns)
    
    total_return = (1 + returns_series).cumprod().iloc[-1] - 1
    sharpe_ratio = returns_series.mean() / returns_series.std() * np.sqrt(252) if returns_series.std() != 0 else 0
    cumulative = (1 + returns_series).cumprod()
    max_drawdown = (cumulative.cummax() - cumulative).max() / cumulative.cummax().max()
    
    print(f"回测期间: {start_date} 至 {end_date}")
    print(f"交易日数量: {len(daily_returns)}")
    print(f"总收益率: {total_return:.2%}")
    print(f"年化收益率: {(1 + total_return) ** (252 / len(daily_returns)) - 1:.2%}")
    print(f"夏普比率: {sharpe_ratio:.2f}")
    print(f"最大回撤: {max_drawdown:.2%}")
    
    return {
        "总收益率": f"{total_return:.2%}",
        "年化收益率": f"{(1 + total_return) ** (252 / len(daily_returns)) - 1:.2%}" if daily_returns else "0.00%",
        "夏普比率": f"{sharpe_ratio:.2f}",
        "最大回撤": f"{max_drawdown:.2%}",
        "交易日数量": len(daily_returns)
    }
