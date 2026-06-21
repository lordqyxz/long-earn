"""FundFlowAnalyst 接口契约测试

覆盖：
1. 构造：从 context 正确注入 LLM / logger / prompt
2. fetch_fund_flow：ciccwm 可用 / 不可用 / 异常的容错路径
3. analyze：DataFrame 非空走 markdown 表 / 空数据走占位提示
"""

from unittest.mock import MagicMock

import pandas as pd

from long_earn.stock_analysis.agents.fund_flow_analyst import FundFlowAnalyst


def _make_context(
    *,
    fund_flow_df: pd.DataFrame | None = None,
    has_ciccwm: bool = True,
    raise_in_ciccwm: bool = False,
) -> MagicMock:
    """构造测试用 context；可控注入 ciccwm provider 行为"""
    context = MagicMock()
    # LLM
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = MagicMock(content="资金流向分析结果")
    context.require_llm.return_value.get_llm.return_value = fake_llm
    context.logger = MagicMock()

    # data_provider.ciccwm_provider
    provider = MagicMock()
    if has_ciccwm:
        ciccwm = MagicMock()
        if raise_in_ciccwm:
            ciccwm.get_fund_flow.side_effect = RuntimeError("ciccwm down")
        else:
            ciccwm.get_fund_flow.return_value = (
                fund_flow_df if fund_flow_df is not None else pd.DataFrame()
            )
        provider.ciccwm_provider = ciccwm
    else:
        provider.ciccwm_provider = None
    context.data_provider = provider
    return context


class TestFundFlowFetch:
    """fetch_fund_flow 的容错路径"""

    def test_fetch_returns_df_when_ciccwm_available(self):
        df = pd.DataFrame({"date": ["2026-06-22"], "main_net_inflow": [1234567.0]})
        ctx = _make_context(fund_flow_df=df)
        analyst = FundFlowAnalyst(ctx)
        result = analyst.fetch_fund_flow("600519.SH")
        assert not result.empty
        assert "main_net_inflow" in result.columns

    def test_fetch_returns_empty_when_no_ciccwm_provider(self):
        ctx = _make_context(has_ciccwm=False)
        analyst = FundFlowAnalyst(ctx)
        assert analyst.fetch_fund_flow("600519.SH").empty

    def test_fetch_returns_empty_when_no_data_provider(self):
        ctx = MagicMock()
        ctx.require_llm.return_value.get_llm.return_value = MagicMock()
        ctx.logger = MagicMock()
        ctx.data_provider = None
        analyst = FundFlowAnalyst(ctx)
        assert analyst.fetch_fund_flow("600519.SH").empty

    def test_fetch_swallows_exception_returns_empty(self):
        """ciccwm get_fund_flow 抛异常 → 返回空 DataFrame，不向上抛"""
        ctx = _make_context(raise_in_ciccwm=True)
        analyst = FundFlowAnalyst(ctx)
        result = analyst.fetch_fund_flow("600519.SH")
        assert result.empty
        ctx.logger.warning.assert_called_once()


class TestFundFlowAnalyze:
    """analyze() 的格式化与调用契约"""

    def test_analyze_with_explicit_fund_flow_df_skips_fetch(self):
        """显式传入 fund_flow_data 时不再触发 fetch_fund_flow"""
        df = pd.DataFrame({"date": ["2026-06-22"], "main_net_inflow": [1.0]})
        ctx = _make_context()
        analyst = FundFlowAnalyst(ctx)
        result = analyst.analyze({"stock_info": {"symbol": "600519.SH"}}, df)
        assert result == "资金流向分析结果"
        # 显式传 DF 时不应去 ciccwm 拉
        ctx.data_provider.ciccwm_provider.get_fund_flow.assert_not_called()
        # LLM 的 prompt 应包含 DF 的 markdown 表
        prompt_arg = ctx.require_llm().get_llm().invoke.call_args[0][0]
        assert "main_net_inflow" in prompt_arg

    def test_analyze_auto_fetches_when_no_df_provided(self):
        """未传 fund_flow_data 时按 stock_info.symbol 自动拉取"""
        df = pd.DataFrame({"date": ["2026-06-22"], "main_net_inflow": [1.0]})
        ctx = _make_context(fund_flow_df=df)
        analyst = FundFlowAnalyst(ctx)
        analyst.analyze({"stock_info": {"symbol": "600519.SH"}})
        ctx.data_provider.ciccwm_provider.get_fund_flow.assert_called_once_with(
            "600519.SH"
        )

    def test_analyze_empty_df_renders_placeholder_in_prompt(self):
        """资金流向数据为空时 prompt 走占位提示，不抛异常"""
        ctx = _make_context()  # 默认空 DF
        analyst = FundFlowAnalyst(ctx)
        result = analyst.analyze({"stock_info": {"symbol": "600519.SH"}})
        assert result == "资金流向分析结果"
        prompt_arg = ctx.require_llm().get_llm().invoke.call_args[0][0]
        assert "无资金流向数据" in prompt_arg

    def test_analyze_no_symbol_skips_fetch(self):
        """stock_info 中无 symbol/code 时直接跳过 fetch，走空数据占位"""
        ctx = _make_context()
        analyst = FundFlowAnalyst(ctx)
        analyst.analyze({"stock_info": {}})
        ctx.data_provider.ciccwm_provider.get_fund_flow.assert_not_called()
