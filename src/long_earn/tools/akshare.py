from typing import Dict, Any


def get_stock_data(stock_code: str) -> Dict[str, Any]:
    """获取股票数据"""
    try:
        # 尝试导入akshare
        import akshare as ak
    except ImportError:
        # 如果akshare未安装，返回错误信息
        error_msg = "错误: akshare库未安装。请运行 'pip install akshare' 安装依赖。"
        print(error_msg)
        return {
            "error": error_msg,
            "code": stock_code,
            "name": "工具错误",
            "current_price": 0.0,
            "change_percent": 0.0,
            "volume": 0,
            "turnover": 0.0,
            "market_cap": 0.0,
            "pe_ratio": 0.0,
            "pb_ratio": 0.0,
            "company_info": {},
            "financial_metrics": {},
            "historical_data": {}
        }
    
    try:
        # 获取A股实时行情数据
        stock_info = ak.stock_zh_a_spot_em()
        
        # 根据股票代码筛选数据
        stock_row = stock_info[stock_info['代码'] == stock_code]
        
        if stock_row.empty:
            # 如果没有找到对应股票，返回错误信息
            error_msg = f"错误: 未找到股票代码 {stock_code} 的数据。"
            print(error_msg)
            return {
                "error": error_msg,
                "code": stock_code,
                "name": "未找到股票",
                "current_price": 0.0,
                "change_percent": 0.0,
                "volume": 0,
                "turnover": 0.0,
                "market_cap": 0.0,
                "pe_ratio": 0.0,
                "pb_ratio": 0.0,
                "company_info": {},
                "financial_metrics": {},
                "historical_data": {}
            }
        
        # 提取股票数据
        stock_data = stock_row.iloc[0]
        
        # 获取更详细的股票信息
        code = stock_code
        name = stock_data.get('名称', '未知股票')
        current_price = float(stock_data.get('最新价', 0.0))
        change_percent = float(stock_data.get('涨跌幅', 0.0))
        volume = int(stock_data.get('成交量', 0))
        turnover = float(stock_data.get('成交额', 0.0))
        
        # 尝试获取更多财务数据
        pe_ratio = float(stock_data.get('市盈率', 0.0)) if stock_data.get('市盈率') != '-' else 0.0
        pb_ratio = float(stock_data.get('市净率', 0.0)) if stock_data.get('市净率') != '-' else 0.0
        
        return {
            "code": code,
            "name": name,
            "current_price": current_price,
            "change_percent": change_percent,
            "volume": volume,
            "turnover": turnover,
            "market_cap": float(stock_data.get('市值', 0.0)) if stock_data.get('市值') != '-' else 0.0,
            "pe_ratio": pe_ratio,
            "pb_ratio": pb_ratio,
            "company_info": {
                "business": "暂无详细业务信息",
                "industry": stock_data.get('行业', '未知行业'),
                "location": "暂无位置信息"
            },
            "financial_metrics": {
                "revenue_growth": "暂无",
                "profit_margin": "暂无",
                "roe": float(stock_data.get('ROE加权', 0.0)) if stock_data.get('ROE加权') != '-' else 0.0,
                "debt_to_equity": "暂无"
            },
            "historical_data": {
                "week_high": "暂无",
                "week_low": "暂无",
                "month_performance": "暂无"
            }
        }
    except Exception as e:
        # 如果出现错误，返回错误信息
        error_msg = f"获取股票数据时出错: {str(e)}"
        print(error_msg)
        return {
            "error": error_msg,
            "code": stock_code,
            "name": "数据获取失败",
            "current_price": 0.0,
            "change_percent": 0.0,
            "volume": 0,
            "turnover": 0.0,
            "market_cap": 0.0,
            "pe_ratio": 0.0,
            "pb_ratio": 0.0,
            "company_info": {},
            "financial_metrics": {},
            "historical_data": {}
        }


def get_index_data(index_code: str) -> Dict[str, Any]:
    """获取指数数据"""
    try:
        import akshare as ak
    except ImportError:
        # 如果akshare未安装，返回错误信息
        error_msg = "错误: akshare库未安装。请运行 'pip install akshare' 安装依赖。"
        print(error_msg)
        return {
            "error": error_msg,
            "code": index_code,
            "name": "工具错误",
            "price": 0.0,
            "change": 0.0,
            "volume": 0
        }
    
    # 实现akshare获取指数数据的逻辑
    # 这里返回错误信息，因为当前未实现真实数据获取
    error_msg = f"错误: 暂不支持获取指数 {index_code} 的数据。"
    print(error_msg)
    return {
        "error": error_msg,
        "code": index_code,
        "name": "未实现",
        "price": 0.0,
        "change": 0.0,
        "volume": 0
    }
