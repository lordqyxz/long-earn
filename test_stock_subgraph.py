import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.long_earn.stock_analysis.subgraph import create_stock_analysis_subgraph

# 创建子图
subgraph = create_stock_analysis_subgraph()

# 测试自然语言查询
print("测试1: 帮我分析卫星化学这个股票")
result = subgraph.invoke({"query": "帮我分析卫星化学这个股票"})
print(f"结果: {result}")
print()

# 测试另一个股票
print("测试2: 帮我分析贵州茅台这个股票")
result = subgraph.invoke({"query": "帮我分析贵州茅台这个股票"})
print(f"结果: {result}")
print()

# 测试直接提供股票代码
print("测试3: 直接提供股票代码")
result = subgraph.invoke({"stock_code": "600519"})
print(f"结果: {result}")
