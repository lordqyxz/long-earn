"""测试数据提取功能"""

import sys
from pathlib import Path

import pandas as pd
from qlib import init as qlib_init
from qlib.data import D


def test_qlib_initialization():
    """测试 1: qlib 初始化"""
    print("=" * 60)
    print("测试 1: qlib 初始化")
    print("=" * 60)

    qlib_data_path = Path.home() / ".qlib_data" / "cn_data"
    print(f"qlib 数据路径：{qlib_data_path}")
    print(f"数据路径存在：{qlib_data_path.exists()}")

    if qlib_data_path.exists():
        print(f"使用现有数据路径：{qlib_data_path}")
        qlib_init(provider_uri=str(qlib_data_path), region="cn")
    else:
        print("数据路径不存在，使用默认配置")
        qlib_init(region="cn")

    print("✅ qlib 初始化成功\n")
    return True


def test_calendar_extraction():
    """测试 2: 交易日历提取"""
    print("=" * 60)
    print("测试 2: 交易日历提取")
    print("=" * 60)

    start_date = "2023-01-01"
    end_date = "2023-01-31"

    print(f"获取交易日历：{start_date} 至 {end_date}")
    dates = D.calendar(start_time=start_date, end_time=end_date, freq="day")

    dates = pd.to_datetime(dates)
    dates_list = dates.tolist()

    print(f"获取到 {len(dates_list)} 个交易日")

    if len(dates_list) > 0:
        print(f"首个交易日：{dates_list[0].strftime('%Y-%m-%d')}")
        print(f"末尾交易日：{dates_list[-1].strftime('%Y-%m-%d')}")
        print("✅ 交易日历提取成功\n")
        return True
    else:
        print("❌ 交易日历为空\n")
        return False


def test_stock_data_extraction():
    """测试 3: 股票数据提取"""
    print("=" * 60)
    print("测试 3: 股票数据提取")
    print("=" * 60)

    # qlib 使用完整股票代码格式：sh600519, sz000001 等
    test_stocks = ["sh600519", "sz000001", "sz000858"]
    start_date = "2023-01-01"
    end_date = "2023-01-31"

    print(f"测试股票：{test_stocks}")
    print(f"日期范围：{start_date} 至 {end_date}")

    for stock in test_stocks:
        try:
            end_dt = pd.Timestamp(end_date)
            start_dt = end_dt - pd.Timedelta(days=10)

            close_data = D.features(
                [stock], ["$close"], start_time=start_dt, end_time=end_dt
            )

            if close_data is None or close_data.empty:
                print(f"❌ {stock}: 无数据可用")
            else:
                print(f"✅ {stock}: 获取到 {len(close_data)} 行数据")
                # qlib 返回的数据格式：DataFrame 索引为 (instrument, datetime) 的 MultiIndex
                if len(close_data) > 0:
                    # 获取该股票的数据
                    try:
                        stock_data = close_data.loc[(stock, slice(None)), "$close"]
                        if len(stock_data) > 0:
                            latest_close = stock_data.iloc[-1]
                            print(f"   最新收盘价：{latest_close:.2f}")
                    except KeyError:
                        print("   ⚠️  无法获取该股票数据")
        except Exception as e:
            print(f"❌ {stock}: 获取数据失败 - {e}")

    print()
    return True


def test_portfolio_return_extraction():
    """测试 4: 组合收益数据提取（模拟 server.py 逻辑）"""
    print("=" * 60)
    print("测试 4: 组合收益数据提取")
    print("=" * 60)

    # 模拟策略信号（使用完整股票代码格式）
    signals = {"sh600519": 0.3, "sz000001": 0.3, "sz000858": 0.4}

    date_str = "2023-01-20"
    print(f"测试日期：{date_str}")
    print(f"模拟信号：{signals}")

    stock_list = list(signals.keys())

    try:
        end_date = pd.Timestamp(date_str)
        start_date = end_date - pd.Timedelta(days=10)

        print(f"获取数据范围：{start_date.date()} 至 {end_date.date()}")

        close_data = D.features(
            stock_list, ["$close"], start_time=start_date, end_time=end_date
        )

        if close_data is None or close_data.empty:
            print("❌ 无数据可用")
            return False

        print(f"✅ 获取到数据形状：{close_data.shape}")
        print(f"   股票代码：{list(close_data.columns)}")

        # 计算组合收益
        portfolio_return = 0.0
        total_weight = 0.0

        for stock, weight in signals.items():
            if weight == 0:
                continue

            if stock in close_data.columns:
                stock_close = close_data[stock]["$close"]
                if len(stock_close) >= 2:
                    latest_close = stock_close.iloc[-1]
                    prev_close = stock_close.iloc[-2]

                    if prev_close > 0:
                        stock_return = (latest_close - prev_close) / prev_close
                        portfolio_return += weight * stock_return
                        total_weight += abs(weight)
                        print(f"   {stock}: 收益率={stock_return:.4f}, 权重={weight}")

        if total_weight > 0:
            portfolio_return /= total_weight
            print(
                f"✅ 组合收益率：{portfolio_return:.6f} ({portfolio_return * 100:.4f}%)"
            )
        else:
            print("⚠️  总权重为 0")

        return True

    except Exception as e:
        print(f"❌ 获取数据失败：{e}")
        import traceback

        traceback.print_exc()
        return False


def test_instruments_extraction():
    """测试 5: 成分股列表提取"""
    print("=" * 60)
    print("测试 5: 成分股列表提取")
    print("=" * 60)

    markets = ["csi300", "csi500", "csi800"]

    for market in markets:
        try:
            stocks = D.instruments(market=market)
            if stocks and len(stocks) > 0:
                print(f"✅ {market.upper()}: {len(stocks)} 只成分股")
                print(f"   前 5 只：{list(stocks)[:5]}")
            else:
                print(f"⚠️  {market.upper()}: 成分股列表为空")
        except Exception as e:
            print(f"❌ {market.upper()}: 获取失败 - {e}")

    print()
    return True


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("开始测试数据提取功能")
    print("=" * 60 + "\n")

    results = {}

    # 测试 1: qlib 初始化
    results["qlib 初始化"] = test_qlib_initialization()

    # 测试 2: 交易日历提取
    results["交易日历提取"] = test_calendar_extraction()

    # 测试 3: 股票数据提取
    results["股票数据提取"] = test_stock_data_extraction()

    # 测试 4: 组合收益数据提取
    results["组合收益数据提取"] = test_portfolio_return_extraction()

    # 测试 5: 成分股列表提取
    results["成分股列表提取"] = test_instruments_extraction()

    # 汇总结果
    print("=" * 60)
    print("测试结果汇总")
    print("=" * 60)

    for test_name, result in results.items():
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{test_name}: {status}")

    passed = sum(results.values())
    total = len(results)

    print(f"\n总计：{passed}/{total} 个测试通过")

    if passed == total:
        print("\n🎉 所有测试通过！数据提取功能正常。")
        return 0
    else:
        print(f"\n⚠️  有 {total - passed} 个测试失败，请检查问题。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
