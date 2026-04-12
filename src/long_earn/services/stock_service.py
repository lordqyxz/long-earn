"""股票数据服务实现

封装 akshare 股票数据获取功能。
"""

import re

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import akshare as ak

from long_earn.services import StockService

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext
    from long_earn.services import LoggerService


class StockServiceImpl(StockService):
    """股票数据服务实现

    参考 LangGraph Runtime 实践：
    1. 依赖通过 context 传递
    2. 便于测试时 Mock
    3. 统一错误处理
    """

    def __init__(self, context: "RuntimeContext"):
        """初始化股票服务

        Args:
            context: 运行时上下文
        """
        self.context = context
        self.logger = context.logger

    def get_stock_code_by_name(self, stock_name: str) -> str:
        """从 NLP 回答中提取股票代码

        Args:
            stock_name: 股票名称

        Returns:
            股票代码
        """
        try:
            nlp_answer_df = ak.nlp_answer(question=stock_name + "")
            stock_code_match = re.search(r"[0-9]+", nlp_answer_df)
            if stock_code_match:
                return stock_code_match.group(0)
            return ""
        except Exception as e:
            if self.logger:
                self.logger.exception(f"获取股票代码失败：{e}")
            return ""

    def get_stock_data(self, stock_code: str) -> dict[str, Any]:
        """获取股票数据

        Args:
            stock_code: 股票代码

        Returns:
            股票数据字典
        """
        try:
            stock_info = ak.stock_individual_info_em(symbol=stock_code)

            if stock_info.empty:
                error_msg = f"错误：未找到股票代码 {stock_code} 的数据。"
                if self.logger:
                    self.logger.error(error_msg)
                return {
                    "error": error_msg,
                    "code": stock_code,
                    "name": "未找到股票",
                }

            stock_dict = dict(zip(stock_info["item"], stock_info["value"]))

            return {
                "code": stock_code,
                "name": stock_dict.get("股票简称", "未知股票"),
                "current_price": float(stock_dict.get("最新", 0.0)),
                "change_percent": 0.0,
                "volume": 0,
                "turnover": 0.0,
                "total_shares": float(stock_dict.get("总股本", 0.0)),
                "circulating_shares": float(stock_dict.get("流通股", 0.0)),
                "total_market_value": float(stock_dict.get("总市值", 0.0)),
                "circulating_market_value": float(stock_dict.get("流通市值", 0.0)),
                "listing_date": stock_dict.get("上市时间", ""),
                "company_info": {
                    "business": "暂无详细业务信息",
                    "industry": stock_dict.get("行业", "未知行业"),
                    "location": "暂无位置信息",
                },
            }
        except Exception as e:
            error_msg = f"获取股票数据时出错：{str(e)}"
            if self.logger:
                self.logger.exception(error_msg)
            return {
                "error": error_msg,
                "code": stock_code,
                "name": "数据获取失败",
            }

    def get_financial_metrics(
        self, stock_code: str = "600519", start_year: str = "2021"
    ) -> dict[str, Any]:
        """获取股票财务指标

        Args:
            stock_code: 股票代码
            start_year: 起始年份

        Returns:
            财务指标字典
        """
        try:
            financial_df = ak.stock_financial_analysis_indicator(
                symbol=stock_code, start_year=start_year
            )

            if financial_df.empty:
                error_msg = f"错误：未找到股票代码 {stock_code} 的财务指标数据。"
                if self.logger:
                    self.logger.error(error_msg)
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
                    "eps": latest_data.get("摊薄每股收益 (元)", 0.0),
                    "eps_weighted": latest_data.get("加权每股收益 (元)", 0.0),
                    "bvps": latest_data.get("每股净资产_调整前 (元)", 0.0),
                    "operating_cash_flow_per_share": latest_data.get(
                        "每股经营性现金流 (元)", 0.0
                    ),
                    "capital_reserve_per_share": latest_data.get(
                        "每股资本公积金 (元)", 0.0
                    ),
                    "retained_earnings_per_share": latest_data.get(
                        "每股未分配利润 (元)", 0.0
                    ),
                    "total_assets": latest_data.get("资产总计", 0.0),
                    "total_liabilities": latest_data.get("负债合计", 0.0),
                    "equity": latest_data.get("股东权益合计", 0.0),
                },
                "raw_data": financial_df.to_dict(orient="records"),
            }
        except Exception as e:
            error_msg = f"获取股票财务指标时出错：{str(e)}"
            if self.logger:
                self.logger.exception(error_msg)
            return {
                "error": error_msg,
                "code": stock_code,
                "name": "数据获取失败",
                "financial_metrics": {},
            }

    def get_price_history(self, stock_code: str) -> list:
        """获取股票交易信息（近五年的月线数据）

        Args:
            stock_code: 股票代码

        Returns:
            交易信息列表
        """
        try:
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
        except Exception as e:
            if self.logger:
                self.logger.exception(f"获取价格历史失败：{e}")
            return []
