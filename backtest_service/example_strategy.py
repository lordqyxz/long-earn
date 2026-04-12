"""双均线交叉策略示例

策略逻辑：
- 当短期均线上穿长期均线时，买入
- 当短期均线下穿长期均线时，卖出
- 使用 5 日和 20 日均线

适用场景：
- 趋势跟踪
- 中等频率交易

参数：
- short_window: 短期均线窗口（默认 5 日）
- long_window: 长期均线窗口（默认 20 日）
- stock_list: 股票列表

返回：
- 交易信号字典：{股票代码：权重}
- 权重范围：-1.0（做空）到 1.0（做多），0 表示空仓
"""

from qlib.data import D
import pandas as pd
import numpy as np


class DualMovingAverageStrategy:
    """双均线交叉策略类"""
    
    def __init__(self, stock_list=None, short_window=5, long_window=20):
        """
        初始化策略
        
        Args:
            stock_list: 股票列表，默认为 ["sh600519"]
            short_window: 短期均线窗口，默认 5 日
            long_window: 长期均线窗口，默认 20 日
        """
        self.stock_list = stock_list or ["sh600519"]
        self.short_window = short_window
        self.long_window = long_window
    
    def generate_signals(self, date_str):
        """
        生成交易信号
        
        Args:
            date_str: 日期字符串，格式 "YYYY-MM-DD"
            
        Returns:
            dict: 交易信号 {股票代码：权重}
        """
        signals = {}
        
        # 计算需要的数据窗口
        lookback_days = int(self.long_window * 1.5)  # 增加 50% 缓冲
        end_date = pd.Timestamp(date_str)
        start_date = end_date - pd.Timedelta(days=lookback_days)
        
        for stock in self.stock_list:
            try:
                # 获取收盘价数据
                df = D.features([stock], ["$close"], 
                               start_time=start_date, 
                               end_time=end_date)
                
                if df is None or len(df) < self.long_window:
                    # 数据不足，保持空仓
                    signals[stock] = 0.0
                    continue
                
                # 提取该股票的数据
                stock_data = df.loc[(stock, slice(None)), "$close"]
                
                if len(stock_data) < self.long_window:
                    signals[stock] = 0.0
                    continue
                
                # 计算均线
                short_ma = stock_data.rolling(window=self.short_window).mean()
                long_ma = stock_data.rolling(window=self.long_window).mean()
                
                # 获取最新值和前值
                current_short_ma = short_ma.iloc[-1]
                current_long_ma = long_ma.iloc[-1]
                prev_short_ma = short_ma.iloc[-2]
                prev_long_ma = long_ma.iloc[-2]
                
                # 判断金叉和死叉
                if prev_short_ma <= prev_long_ma and current_short_ma > current_long_ma:
                    # 金叉：买入信号
                    signals[stock] = 1.0
                elif prev_short_ma >= prev_long_ma and current_short_ma < current_long_ma:
                    # 死叉：卖出信号
                    signals[stock] = 0.0
                else:
                    # 保持现有仓位
                    if current_short_ma > current_long_ma:
                        signals[stock] = 1.0  # 持有多头
                    else:
                        signals[stock] = 0.0  # 空仓
                        
            except Exception as e:
                # 出现异常时返回空仓
                signals[stock] = 0.0
        
        return signals


# 策略版本信息
__version__ = "1.0.0"
