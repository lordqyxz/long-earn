"""股票数据服务实现（miniqmt 版）

封装 xtquant.xtdata 股票数据获取功能。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from long_earn.backtest.data.miniqmt_provider import MiniQmtClient
from long_earn.services import StockService

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext


class StockServiceImpl(StockService):
    """股票数据服务实现（miniqmt 版）

    使用 xtquant.xtdata 获取股票信息、财务数据、K线数据。
    """

    def __init__(self, context: RuntimeContext):
        self.context = context
        self.logger = context.logger
        self._client = MiniQmtClient.get()

    def get_stock_code_by_name(self, stock_name: str) -> str:
        """通过板块搜索匹配股票名称。

        xtquant 没有 NLP 接口，改为通过板块遍历匹配。
        """
        try:
            # 尝试从常用板块搜索
            for sector in ["沪深300", "中证500", "上证50"]:
                stocks = self._client.get_sector_stocks(sector)
                for code in stocks:
                    detail = self._client.get_instrument_detail(code)
                    if stock_name in str(detail.get("stockName", "")):
                        return code
        except Exception as e:
            if self.logger:
                self.logger.warning(f"股票名称搜索失败: {e}")
        return ""

    def get_stock_data(self, stock_code: str) -> dict[str, Any]:
        """获取股票基本信息。"""
        try:
            detail = self._client.get_instrument_detail(stock_code)
            if not detail:
                return {
                    "error": f"未找到股票代码 {stock_code}",
                    "code": stock_code,
                    "name": "未找到",
                }

            tick = self._client.get_full_tick([stock_code])

            return {
                "code": stock_code,
                "name": detail.get("stockName", detail.get("name", "未知")),
                "current_price": float(tick.get(stock_code, {}).get("latestPrice", 0.0)),
                "change_percent": float(tick.get(stock_code, {}).get("changeRatio", 0.0)),
                "volume": int(tick.get(stock_code, {}).get("volume", 0)),
                "turnover": float(tick.get(stock_code, {}).get("amount", 0.0)),
                "total_shares": float(detail.get("totalShare", 0.0)),
                "circulating_shares": float(detail.get("floatShare", 0.0)),
                "total_market_value": float(detail.get("marketValue", 0.0)),
                "circulating_market_value": float(detail.get("flowMarketValue", 0.0)),
                "listing_date": detail.get("listDate", ""),
                "company_info": {
                    "business": "暂无详细业务信息",
                    "industry": detail.get("industry", "未知行业"),
                    "location": detail.get("region", "未知地区"),
                },
            }
        except Exception as e:
            if self.logger:
                self.logger.exception(f"获取股票数据失败: {e}")
            return {
                "error": str(e),
                "code": stock_code,
                "name": "数据获取失败",
            }

    def get_financial_metrics(
        self, stock_code: str = "600519", start_year: str = "2021"
    ) -> dict[str, Any]:
        """获取股票财务指标。"""
        try:
            end_date = datetime.now().strftime("%Y%m%d")
            df = self._client.get_financial(
                stock_list=[stock_code],
                start_time=start_year + "0101",
                end_time=end_date,
                table="Balance",
            )

            if df.empty:
                return {
                    "error": f"未找到 {stock_code} 财务数据",
                    "code": stock_code,
                    "name": "未找到",
                    "financial_metrics": {},
                }

            latest = df.iloc[0] if len(df) > 0 else {}
            return {
                "code": stock_code,
                "report_date": str(latest.get("report_date", "")),
                "financial_metrics": {
                    "eps": float(latest.get("eps", 0.0)),
                    "roe": float(latest.get("roe", 0.0)),
                    "revenue": float(latest.get("operating_revenue", 0.0)),
                    "net_profit": float(latest.get("net_profit", 0.0)),
                },
                "raw_data": df.to_dict(orient="records"),
            }
        except Exception as e:
            if self.logger:
                self.logger.exception(f"获取财务指标失败: {e}")
            return {
                "error": str(e),
                "code": stock_code,
                "name": "数据获取失败",
                "financial_metrics": {},
            }

    def get_price_history(self, stock_code: str) -> list:
        """获取股票历史 K 线（近五年月线）。"""
        try:
            end = datetime.now()
            start = end - timedelta(days=5 * 365)
            df = self._client.get_kline(
                stock_list=[stock_code],
                start_time=start.strftime("%Y%m%d"),
                end_time=end.strftime("%Y%m%d"),
                period="1M",
            )
            if df.empty:
                return []
            records = df.to_dict(orient="records")
            for r in records:
                r["date"] = r.get("date", "")
                r["open"] = r.get("open", 0)
                r["high"] = r.get("high", 0)
                r["low"] = r.get("low", 0)
                r["close"] = r.get("close", 0)
                r["volume"] = r.get("volume", 0)
            return records
        except Exception as e:
            if self.logger:
                self.logger.exception(f"获取价格历史失败: {e}")
            return []
