# 简单均线策略 - 后备策略
import pandas as pd
from qlib.data import D
from qlib import init
from pathlib import Path

# 初始化 qlib
qlib_data_path = Path.home() / ".qlib_data"
if qlib_data_path.exists():
    init(provider_uri=str(qlib_data_path), region="cn")


class MomentumStrategy:
    """简单的动量策略：基于价格动量生成交易信号"""
    
    def __init__(self, short_window: int = 5, long_window: int = 20):
        """初始化策略参数
        
        Args:
            short_window: 短期窗口长度
            long_window: 长期窗口长度
        """
        self.short_window = short_window
        self.long_window = long_window
        self.stock_list = None
    
    def generate_signals(self, date: str) -> pd.Series:
        """生成交易信号
        
        Args:
            date: 交易日期
            
        Returns:
            pd.Series: 交易信号，索引为股票代码，值为仓位权重
        """
        # 获取股票列表
        if self.stock_list is None:
            self.stock_list = D.instruments(market='csi300')
        
        # 获取历史数据
        try:
            # 获取收盘价数据
            close_data = D.features(
                self.stock_list[:50],  # 只使用前 50 只股票
                ['$close'],
                start_time=pd.Timestamp(date) - pd.Timedelta(days=self.long_window * 2),
                end_time=pd.Timestamp(date)
            )
            
            # 计算动量信号
            signals = {}
            for stock in self.stock_list[:50]:
                if stock in close_data.columns:
                    stock_data = close_data[stock]
                    if len(stock_data) >= self.long_window:
                        # 计算短期和长期均线
                        short_ma = stock_data['$close'].rolling(window=self.short_window).mean().iloc[-1]
                        long_ma = stock_data['$close'].rolling(window=self.long_window).mean().iloc[-1]
                        
                        # 金叉买入，死叉卖出
                        if short_ma > long_ma:
                            signals[stock] = 1.0  # 买入信号
                        else:
                            signals[stock] = 0.0  # 空仓
                    else:
                        signals[stock] = 0.0
                else:
                    signals[stock] = 0.0
            
            return pd.Series(signals)
            
        except Exception as e:
            print(f"生成信号失败：{e}")
            return pd.Series({})
