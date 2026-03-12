import re

from datetime import datetime, timedelta
from typing import Any, Dict

import akshare as ak

from long_earn.utils import LOGGER


def get_stock_code_by_name(stock_name: str) -> str:
    """从NLP回答中提取股票代码"""
    nlp_answer_df = ak.nlp_answer(question=stock_name + "")
    stock_code_match = re.search(r"[0-9]+", nlp_answer_df)
    if stock_code_match:
        return stock_code_match.group(0)
    return ""


def get_market_info():
    """Dataframe
              项目     股票       科创板         主板
    0   流通股本   40403.47    413.63   39989.84
    1    总市值  516714.68   55719.6  460995.09
    2  平均市盈率      17.92      71.0      16.51
    3   上市公司       2036       377       1659
    4   上市股票       2078       377       1701
    5   流通市值  432772.13   22274.3  410497.83
    6   报告时间   20211230  20211230   20211230
    8    总股本   46234.03    1211.5   45022.54

    """
    stock_sse_summary_df = ak.stock_sse_summary()
    # 转字典
    return stock_sse_summary_df.set_index("项目").to_dict(orient="index")


def get_stock_data(stock_code: str) -> Dict[str, Any]:
    """获取股票数据
        返回字段:
           item               value
    0    最新                4.66
    1  股票代码            000002
    2  股票简称             万  科Ａ
    3   总股本      11930709471.0
    4   流通股       9716399629.0
    5   总市值   55597106134.86
    6  流通市值   45278422271.14
    7    行业             房地产开发
    8   上市时间           19910129
    """
    try:
        # 根据akshare官方文档，直接调用stock_individual_info_em获取股票信息
        stock_info = ak.stock_individual_info_em(symbol=stock_code)
        # 检查返回的数据是否为空
        if stock_info.empty:
            # 如果没有找到对应股票，返回错误信息
            error_msg = f"错误: 未找到股票代码 {stock_code} 的数据。"
            LOGGER.error(error_msg)
            return {
                "error": error_msg,
                "code": stock_code,
                "name": "未找到股票",
            }

        # 根据akshare返回的DataFrame格式，转换为字典
        stock_dict = dict(zip(stock_info["item"], stock_info["value"]))

        code = stock_code
        name = stock_dict.get("股票简称", "未知股票")
        current_price = float(stock_dict.get("最新", 0.0))

        total_shares = float(stock_dict.get("总股本", 0.0))
        circulating_shares = float(stock_dict.get("流通股", 0.0))
        total_market_value = float(stock_dict.get("总市值", 0.0))
        circulating_market_value = float(stock_dict.get("流通市值", 0.0))

        industry = stock_dict.get("行业", "未知行业")
        listing_date = stock_dict.get("上市时间", "")

        return {
            "code": code,
            "name": name,
            "current_price": current_price,
            "change_percent": 0.0,
            "volume": 0,
            "turnover": 0.0,
            "total_shares": total_shares,
            "circulating_shares": circulating_shares,
            "total_market_value": total_market_value,
            "circulating_market_value": circulating_market_value,
            "listing_date": listing_date,
            "company_info": {
                "business": "暂无详细业务信息",
                "industry": industry,
                "location": "暂无位置信息",
            },
        }
    except Exception as e:
        # 如果出现错误，返回错误信息
        error_msg = f"获取股票数据时出错: {str(e)}"
        LOGGER.exception(error_msg)
        return {
            "error": error_msg,
            "code": stock_code,
            "name": "数据获取失败",
        }


def get_financial_metrics(
    stock_code: str = "600519", start_year: str = "2021"
) -> Dict[str, Any]:
    """获取股票财务指标

    Args:
        stock_code: 股票代码，如 "600519" 或 "000001"
        start_year: 起始年份，如 "2020"

    Returns:
        包含财务指标的字典
    """
    try:
        financial_df = ak.stock_financial_analysis_indicator(
            symbol=stock_code, start_year=start_year
        )

        if financial_df.empty:
            error_msg = f"错误: 未找到股票代码 {stock_code} 的财务指标数据。"
            LOGGER.error(error_msg)
            return {
                "error": error_msg,
                "code": stock_code,
                "name": "未找到股票",
                "financial_metrics": {},
            }

        latest_data = financial_df.iloc[0].to_dict()

        return {
            "code": stock_code,
            "report_date": latest_data.get("日期", ""),
            "financial_metrics": {
                "eps": latest_data.get("摊薄每股收益(元)", 0.0),
                "eps_weighted": latest_data.get("加权每股收益(元)", 0.0),
                "bvps": latest_data.get("每股净资产_调整前(元)", 0.0),
                "operating_cash_flow_per_share": latest_data.get(
                    "每股经营性现金流(元)", 0.0
                ),
                "capital_reserve_per_share": latest_data.get("每股资本公积金(元)", 0.0),
                "retained_earnings_per_share": latest_data.get(
                    "每股未分配利润(元)", 0.0
                ),
                "total_assets": latest_data.get("资产总计", 0.0),
                "total_liabilities": latest_data.get("负债合计", 0.0),
                "equity": latest_data.get("股东权益合计", 0.0),
            },
            "raw_data": financial_df.to_dict(orient="records"),
        }
    except Exception as e:
        error_msg = f"获取股票财务指标时出错: {str(e)}"
        LOGGER.exception(error_msg)
        return {
            "error": error_msg,
            "code": stock_code,
            "name": "数据获取失败",
            "financial_metrics": {},
        }


def get_price_history(stock_code: str) -> list:
    """
    获取股票交易信息（近五年的月线数据）

    Args:
        stock_code: 股票代码，如 "600519" 或 "000001"

    Returns:
        包含交易信息的字典列表
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=5 * 365)

    start_date_str = start_date.strftime("%Y%m%d")
    end_date_str = end_date.strftime("%Y%m%d")

    stock_kc_a_spot_em_df = ak.stock_zh_a_hist(
        symbol=stock_code,
        period="monthly",
        start_date=start_date_str,
        end_date=end_date_str,
        adjust="qfq",
    )
    return stock_kc_a_spot_em_df.to_dict(orient="records")


def _format_stock_symbol(stock_code: str) -> str:
    """格式化股票代码为 akshare 需要的格式

    Args:
        stock_code: 股票代码，如 "600519" 或 "000001"

    Returns:
        格式化后的股票代码，如 "SH600519" 或 "SZ000001"
    """
    if stock_code.startswith("SH") or stock_code.startswith("SZ"):
        return stock_code

    if stock_code.startswith("6"):
        return f"SH{stock_code}"
    elif stock_code.startswith(("0", "3")):
        return f"SZ{stock_code}"
    else:
        return f"SH{stock_code}"


if __name__ == "__main__":
    stock_code = "600519"
    # financial_metrics = get_financial_metrics(stock_code)
    # print(financial_metrics)
    price_history = get_price_history(stock_code)
    print(price_history)
