"""资金流向视角分析师（ADR-011 阶段 2）。

第 5 个分析视角，补齐 CLAUDE.md TODO「增强分析视角：资金流向分析」。
其他 4 个分析师（巴菲特/芒格/费雪/林奇）专注基本面与估值，本视角专注
**主力资金博弈与情绪面**——ciccwm 提供的资金流向是其他 3 个 Provider
（miniqmt / akshare）均不具备的独占能力。

数据获取容错：
- ciccwm 不可用（凭证缺失 / 网络失败）→ ``fund_flow_data`` 为空 DataFrame
- Prompt 已约定此情形输出"数据暂不可用"占位，不抛异常、不阻塞其他分析师
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd

from long_earn.core.prompt_loader import MarkdownPromptTemplate

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext


class FundFlowAnalyst:
    """资金流向视角分析智能体。

    与既有 4 个分析师保持同构：context 依赖注入 + 单一 ``analyze(stock_data)``
    入口。通过 :class:`MarketIntelligenceProvider` 接口获取资金流向数据
    （ciccwm 独占能力）。
    """

    def __init__(self, context: RuntimeContext) -> None:
        self.context = context
        self.llm = context.require_llm().get_llm()
        self.logger = context.logger
        self.prompt = MarkdownPromptTemplate(
            template_file="fund_flow_prompt.md",
            caller_file=__file__,
            input_variables=["stock_data", "fund_flow_data"],
        )

    def fetch_fund_flow(self, symbol: str) -> pd.DataFrame:
        """通过 MarketIntelligenceProvider 接口获取资金流向。

        ciccwm 不可用或返回空时返回空 DataFrame，不抛异常。
        """
        mi = self.context.market_intelligence
        if mi is None:
            return pd.DataFrame()
        try:
            return mi.get_fund_flow(symbol)
        except Exception as exc:
            self.logger.warning(f"FundFlow 获取失败（symbol={symbol}）: {exc}")
            return pd.DataFrame()

    def analyze(self, stock_data: dict[str, Any]) -> str:
        """从资金流向视角分析股票。

        Args:
            stock_data: 上游 ``get_stock_data`` 节点输出的基础数据字典

        Returns:
            分析文本（markdown）；数据不可用时返回占位说明
        """
        # 从 stock_data 提取 symbol
        symbol = ""
        info = stock_data.get("stock_info") if isinstance(stock_data, dict) else None
        if isinstance(info, dict):
            symbol = info.get("symbol") or info.get("code") or ""

        fund_flow_data = self.fetch_fund_flow(symbol) if symbol else pd.DataFrame()

        # DataFrame → 紧凑文本表示，避免巨大 prompt
        if fund_flow_data is None or fund_flow_data.empty:
            ff_text = "（无资金流向数据，ciccwm 可能不可用）"
        else:
            tail = fund_flow_data.tail(20)
            try:
                ff_text = tail.to_markdown(index=False)
            except Exception:
                ff_text = tail.to_string(index=False)

        formatted_prompt = self.prompt.format(
            stock_data=stock_data,
            fund_flow_data=ff_text,
        )
        response = self.llm.invoke(formatted_prompt)
        return response.content if hasattr(response, "content") else str(response)
