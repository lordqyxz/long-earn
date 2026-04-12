"""回测服务集成测试 - 验证完整的数据提取和回测流程"""

import requests
import time
import sys

def test_backtest_service():
    """测试回测服务的完整流程"""
    
    print("=" * 60)
    print("回测服务集成测试")
    print("=" * 60)
    
    base_url = "http://localhost:8001"
    
    # 测试 1: 健康检查
    print("\n1. 健康检查...")
    try:
        response = requests.get(f"{base_url}/health", timeout=5)
        if response.status_code == 200:
            print(f"✅ 服务正常：{response.json()}")
        else:
            print(f"❌ 服务异常：{response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ 服务未启动，请先运行：uv run --active python -m long_earn_backtest")
        return False
    
    # 测试 2: 运行简单回测
    print("\n2. 运行回测测试...")
    
    # 简单的买入持有策略
    strategy_code = '''
"""简单的买入持有策略"""
from qlib.data import D
import pandas as pd

class SimpleStrategy:
    def __init__(self, stock_list=None):
        self.stock_list = stock_list or ["sh600519"]
    
    def generate_signals(self, date_str):
        """生成交易信号"""
        # 简单策略：始终满仓持有
        signals = {}
        for stock in self.stock_list:
            signals[stock] = 1.0
        return signals
'''
    
    backtest_request = {
        "strategy_code": strategy_code,
        "start_date": "2023-01-01",
        "end_date": "2023-01-31",
        "stock_list": ["sh600519", "sz000001"]
    }
    
    try:
        response = requests.post(
            f"{base_url}/api/v1/backtest",
            json=backtest_request,
            timeout=60
        )
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ 回测成功：{result['message']}")
            print(f"   总收益：{result.get('total_return', 'N/A')}")
            print(f"   年化收益：{result.get('annual_return', 'N/A')}")
            print(f"   夏普比率：{result.get('sharpe_ratio', 'N/A')}")
            print(f"   最大回撤：{result.get('max_drawdown', 'N/A')}")
            print(f"   胜率：{result.get('win_rate', 'N/A')}")
            print(f"   交易天数：{result.get('trading_days', 'N/A')}")
            return True
        else:
            print(f"❌ 回测失败：{response.status_code}")
            print(f"   错误信息：{response.json()}")
            return False
            
    except requests.exceptions.Timeout:
        print("❌ 请求超时")
        return False
    except Exception as e:
        print(f"❌ 请求失败：{e}")
        return False


def main():
    """运行集成测试"""
    success = test_backtest_service()
    
    print("\n" + "=" * 60)
    if success:
        print("✅ 集成测试通过！")
        return 0
    else:
        print("❌ 集成测试失败")
        print("\n提示：如果服务未启动，请运行以下命令:")
        print("  cd backtest_service")
        print("  uv run --active python -m long_earn_backtest")
        return 1


if __name__ == "__main__":
    sys.exit(main())
