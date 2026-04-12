"""测试回测功能 - 使用双均线策略"""

import requests
import sys
from pathlib import Path

def run_backtest_test():
    """运行回测测试"""
    
    print("=" * 60)
    print("回测功能测试 - 双均线交叉策略")
    print("=" * 60)
    
    base_url = "http://localhost:8001"
    
    # 读取策略代码
    strategy_path = Path(__file__).parent / "example_strategy.py"
    with open(strategy_path, "r", encoding="utf-8") as f:
        strategy_code = f.read()
    
    print(f"\n策略文件：{strategy_path}")
    print(f"策略代码长度：{len(strategy_code)} 字符")
    
    # 检查服务是否运行
    print("\n1. 检查服务状态...")
    try:
        response = requests.get(f"{base_url}/health", timeout=5)
        if response.status_code == 200:
            print(f"✅ 服务正常运行：{response.json()}")
        else:
            print(f"❌ 服务异常：{response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ 服务未启动！")
        print("\n请先启动回测服务:")
        print("  cd backtest_service")
        print("  uv run --active python -m long_earn_backtest")
        return False
    
    # 运行回测
    print("\n2. 运行回测...")
    print("   策略：双均线交叉策略")
    print("   股票：sh600519 (贵州茅台), sz000001 (平安银行)")
    print("   时间：2023-01-01 至 2023-03-31")
    
    backtest_request = {
        "strategy_code": strategy_code,
        "start_date": "2023-01-01",
        "end_date": "2023-03-31",
        "stock_list": ["sh600519", "sz000001"]
    }
    
    try:
        response = requests.post(
            f"{base_url}/api/v1/backtest",
            json=backtest_request,
            timeout=120
        )
        
        if response.status_code == 200:
            result = response.json()
            print("\n" + "=" * 60)
            print("✅ 回测成功！")
            print("=" * 60)
            
            if result.get("success"):
                print(f"\n📊 回测结果:")
                print(f"   消息：{result.get('message', 'N/A')}")
                print(f"   总收益率：{result.get('total_return', 0)*100:.2f}%")
                print(f"   年化收益率：{result.get('annual_return', 0)*100:.2f}%")
                print(f"   夏普比率：{result.get('sharpe_ratio', 0):.4f}")
                print(f"   最大回撤：{result.get('max_drawdown', 0)*100:.2f}%")
                print(f"   胜率：{result.get('win_rate', 0)*100:.2f}%")
                print(f"   交易天数：{result.get('trading_days', 0)} 天")
                
                # 评估结果
                print("\n📈 策略评估:")
                if result.get('annual_return', 0) > 0.1:
                    print("   ✅ 年化收益率 > 10%")
                else:
                    print("   ⚠️  年化收益率 < 10%")
                
                if result.get('sharpe_ratio', 0) > 0.5:
                    print("   ✅ 夏普比率 > 0.5")
                else:
                    print("   ⚠️  夏普比率 < 0.5")
                
                if result.get('max_drawdown', 1) < 0.2:
                    print("   ✅ 最大回撤 < 20%")
                else:
                    print("   ⚠️  最大回撤 > 20%")
                
                return True
            else:
                print(f"\n❌ 回测失败：{result.get('message', '未知错误')}")
                return False
                
        elif response.status_code == 500:
            error_detail = response.json().get('detail', '未知错误')
            print(f"\n❌ 回测失败 (500): {error_detail}")
            return False
        else:
            print(f"\n❌ 回测失败 ({response.status_code})")
            print(f"   错误信息：{response.json()}")
            return False
            
    except requests.exceptions.Timeout:
        print("\n❌ 请求超时")
        return False
    except Exception as e:
        print(f"\n❌ 请求失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def test_strategy_directly():
    """直接测试策略（不通过 API）"""
    
    print("=" * 60)
    print("策略直接测试（不通过 API）")
    print("=" * 60)
    
    from pathlib import Path
    import importlib.util
    
    # 读取策略代码
    strategy_path = Path(__file__).parent / "example_strategy.py"
    
    print(f"\n加载策略文件：{strategy_path}")
    
    try:
        # 导入策略模块
        spec = importlib.util.spec_from_file_location("example_strategy", strategy_path)
        if spec is None or spec.loader is None:
            print("❌ 无法加载策略模块")
            return False
        
        strategy_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(strategy_module)
        
        # 获取策略类
        strategy_class = None
        for name, obj in strategy_module.__dict__.items():
            if hasattr(obj, "generate_signals"):
                strategy_class = obj
                break
        
        if not strategy_class:
            print("❌ 未找到策略类")
            return False
        
        print(f"✅ 找到策略类：{strategy_class.__name__}")
        
        # 创建策略实例
        strategy = strategy_class(
            stock_list=["sh600519", "sz000001"],
            short_window=5,
            long_window=20
        )
        
        print(f"✅ 策略实例已创建")
        print(f"   股票列表：{strategy.stock_list}")
        print(f"   短期均线：{strategy.short_window} 日")
        print(f"   长期均线：{strategy.long_window} 日")
        
        # 测试生成信号
        test_date = "2023-01-20"
        print(f"\n测试生成信号 (日期：{test_date})...")
        
        signals = strategy.generate_signals(test_date)
        
        if signals:
            print(f"✅ 信号生成成功")
            for stock, weight in signals.items():
                if weight > 0:
                    position = "做多"
                elif weight < 0:
                    position = "做空"
                else:
                    position = "空仓"
                print(f"   {stock}: {weight:.2f} ({position})")
            return True
        else:
            print("❌ 信号为空")
            return False
            
    except Exception as e:
        print(f"❌ 测试失败：{e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主函数"""
    
    print("\n" + "=" * 60)
    print("回测功能测试")
    print("=" * 60)
    
    # 测试 1: 直接测试策略
    print("\n[测试 1] 策略直接测试")
    print("-" * 60)
    strategy_ok = test_strategy_directly()
    
    # 测试 2: API 回测
    print("\n[测试 2] API 回测")
    print("-" * 60)
    api_ok = run_backtest_test()
    
    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    print(f"策略直接测试：{'✅ 通过' if strategy_ok else '❌ 失败'}")
    print(f"API 回测：{'✅ 通过' if api_ok else '❌ 失败 (服务可能未启动)'}")
    
    if strategy_ok:
        print("\n✅ 策略代码正确，可以正常使用")
        
        if not api_ok:
            print("\n提示：如需测试 API 回测，请先启动服务:")
            print("  cd backtest_service")
            print("  uv run --active python -m long_earn_backtest")
        
        return 0
    else:
        print("\n❌ 策略测试失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
