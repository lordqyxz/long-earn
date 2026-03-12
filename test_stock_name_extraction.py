import re
import akshare as ak

# 测试股票名称提取
def test_stock_name_extraction():
    test_queries = [
        "帮我分析卫星化学这个股票",
        "帮我分析贵州茅台这个股票",
        "请分析腾讯控股这个股票"
    ]
    
    for query in test_queries:
        print(f"\n测试查询: {query}")
        # 提取股票名称
        stock_name_match = re.search(r'分析([^\s]+)股票', query)
        if stock_name_match:
            stock_name = stock_name_match.group(1)
            print(f"提取的股票名称: {stock_name}")
            
            # 尝试获取股票代码
            try:
                stock_list = ak.stock_info_a_code_name()
                print(f"股票列表加载成功，共 {len(stock_list)} 只股票")
                
                # 查找股票代码
                found = False
                for index, row in stock_list.iterrows():
                    if stock_name in row['name']:
                        print(f"找到股票: {row['name']}，代码: {row['code']}")
                        found = True
                        break
                
                if not found:
                    print("未找到对应股票")
            except Exception as e:
                print(f"获取股票代码时出错: {str(e)}")
        else:
            print("未提取到股票名称")

if __name__ == "__main__":
    test_stock_name_extraction()
