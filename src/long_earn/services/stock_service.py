"""股票数据服务实现（miniqmt 版）

封装 xtquant.xtdata 股票数据获取功能。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import polars as pl

from long_earn.backtest.data.miniqmt_provider import MiniQmtClient
from long_earn.backtest.data.symbol import normalize_xt
from long_earn.ontology import ConceptQuery, Connector
from long_earn.services import LoggerService, StockService

if TYPE_CHECKING:
    from long_earn.config import AppConfig


class StockServiceImpl(StockService):
    """股票数据服务实现（miniqmt 版）

    使用 xtquant.xtdata 获取股票信息、财务数据、K线数据。
    """

    def __init__(
        self,
        config: AppConfig,
        logger: LoggerService,
        connector: Connector | None = None,
    ):
        self.config = config
        self.logger = logger
        self._client = MiniQmtClient.get()
        # ADR-014 阶段 C：可选注入 Connector，财务指标查询走本体论连接器
        # 未注入时降级到旧 Balance 单表直连（保持向后兼容）
        self._connector = connector

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
                "current_price": float(
                    tick.get(stock_code, {}).get("latestPrice", 0.0)
                ),
                "change_percent": float(
                    tick.get(stock_code, {}).get("changeRatio", 0.0)
                ),
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
        """获取股票财务指标。

        ADR-014 阶段 C：优先走 Connector（概念="盈利能力指标"），由连接器统一
        取数 + PIT + 字段标准化。未注入 Connector 时降级到旧 Balance 单表直连。
        """
        if self._connector is not None:
            return self._get_financial_metrics_via_connector(stock_code, start_year)
        return self._get_financial_metrics_legacy(stock_code, start_year)

    def _get_financial_metrics_via_connector(
        self,
        stock_code: str,
        start_year: str,
    ) -> dict[str, Any]:
        """通过 Connector 获取财务指标（ADR-014 阶段 C 新路径）。"""
        connector = self._connector
        if connector is None:
            return self._get_financial_metrics_legacy(stock_code, start_year)
        try:
            # 规范化 xt_symbol（确保带后缀）
            symbol = (
                stock_code if "." in stock_code else self._normalize_symbol(stock_code)
            )
            result = connector.get_concept(
                ConceptQuery(
                    subject=symbol,
                    aspect="盈利能力",
                    time=f"{start_year}-01-01~latest",
                )
            )
            if not result.provenance or result.provenance == ["unknown"]:
                return {
                    "error": f"未找到 {stock_code} 财务数据",
                    "code": stock_code,
                    "name": "未找到",
                    "financial_metrics": {},
                }
            # Connector 返回 polars DataFrame，转 dict 取最新一行
            data = result.data
            if not isinstance(data, pl.DataFrame) or data.height == 0:
                return {
                    "error": f"未找到 {stock_code} 财务数据",
                    "code": stock_code,
                    "name": "未找到",
                    "financial_metrics": {},
                }
            latest = data.sort("timestamp", descending=True).row(0, named=True)
            metrics = {
                "eps": float(latest.get("eps", 0.0) or 0.0),
                "roe": float(latest.get("roe", 0.0) or 0.0),
                "revenue": float(latest.get("revenue", 0.0) or 0.0),
                "net_profit": float(latest.get("net_profit", 0.0) or 0.0),
            }
            # 补充盈利能力族其他指标
            for extra in (
                "gross_margin",
                "net_profit_margin",
                "net_profit_yoy",
                "revenue_yoy",
            ):
                if extra in latest:
                    metrics[extra] = float(latest[extra] or 0.0)
            return {
                "code": stock_code,
                "report_date": str(latest.get("timestamp", "")),
                "financial_metrics": metrics,
                "related_concepts": [n.label for n in result.related_nodes[:5]],
            }
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Connector 取财务指标失败，降级 legacy: {e}")
            return self._get_financial_metrics_legacy(stock_code, start_year)

    def _get_financial_metrics_legacy(
        self,
        stock_code: str,
        start_year: str,
    ) -> dict[str, Any]:
        """旧路径：Income 表直连（Balance 表无有效 operating_revenue）。"""
        try:
            end_date = datetime.now().strftime("%Y%m%d")
            df = self._client.get_financial(
                stock_list=[stock_code],
                start_time=start_year + "0101",
                end_time=end_date,
                table="Income",
            )

            if df.empty:
                return {
                    "error": f"未找到 {stock_code} 财务数据",
                    "code": stock_code,
                    "name": "未找到",
                    "financial_metrics": {},
                }

            latest = df.iloc[0] if len(df) > 0 else {}
            revenue_raw = latest.get("operating_revenue", latest.get("revenue"))
            revenue: float | None
            if revenue_raw is None or (
                isinstance(revenue_raw, (int, float)) and float(revenue_raw) == 0.0
            ):
                if self.logger:
                    self.logger.warning(
                        f"legacy 财务路径：{stock_code} Income 表无有效 revenue，"
                        "返回 None（勿用 Balance.operating_revenue 恒 0 字段）"
                    )
                revenue = None
            else:
                revenue = float(revenue_raw)

            metrics: dict[str, Any] = {
                "eps": float(latest.get("eps", 0.0)),
                "roe": float(latest.get("roe", 0.0)),
                "net_profit": float(latest.get("net_profit", 0.0)),
            }
            if revenue is not None:
                metrics["revenue"] = revenue

            return {
                "code": stock_code,
                "report_date": str(latest.get("report_date", "")),
                "financial_metrics": metrics,
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

    @staticmethod
    def _normalize_symbol(stock_code: str) -> str:
        """纯 6 位代码补 xtquant 后缀（复用 symbol.normalize_xt 逻辑）。"""
        return normalize_xt(stock_code)

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
