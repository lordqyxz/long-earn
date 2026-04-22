"""策略开发与回测集成测试

重点测试 develop + backtest 两个阶段，research 阶段用 mock 策略替代。

测试层次：
  1. 代码生成（develop）- LLM 能否根据策略描述生成合规代码
  2. 回测服务（backtest）- 回测服务能否正确执行/报错
  3. 端到端（develop → backtest → refine）- 完整流程

运行方式：
  uv run pytest tests/integration/test_develop_backtest.py -v -s
  uv run pytest tests/integration/test_develop_backtest.py -v -s -k test_profit_growth
  uv run pytest tests/integration/test_develop_backtest.py -v -s -k "DevelopAndBacktest"

回测服务管理：
  - 默认：conftest 自动以子进程启动回测服务（日志实时输出）
  - 手动：设置 BACKTEST_SERVICE_MANUAL=1 跳过自动管理，需自行启动服务
"""

import pytest
from dotenv import load_dotenv

load_dotenv()

from long_earn.config import RuntimeContext
from long_earn.context_init import create_runtime_context
from long_earn.core.llm_utils import sanitize_code
from long_earn.strategy_rd.agents.strategy_develop_agent import StrategyDevelopAgent
from long_earn.tools.backtest import check_service_health

# ── 默认股票池（沪深 300 部分成分股，qlib 格式 SH/PREFIX） ──────────────

DEFAULT_STOCK_LIST = [
    "SH600000",
    "SH600036",
    "SH600519",
    "SH600887",
    "SH601318",
    "SH601398",
    "SH601939",
    "SH603259",
    "SZ000001",
    "SZ000333",
    "SZ000651",
    "SZ000858",
    "SZ002415",
    "SZ002714",
    "SZ300015",
]


# ── Mock 策略（替代 research 阶段） ──────────────────────────────────────

MOCK_STRATEGIES = {
    "profit_growth": {
        "strategy_name": "ProfitGrowthStrategy",
        "description": (
            "利润增长选股策略：选择净利润同比增长率为正的股票，等权重配置。"
            "\n核心逻辑："
            "\n1. 遍历股票池(stock_list参数)，获取每只股票的 $net_profit_yoy 指标"
            "\n2. 筛选净利润增长率 > 5% (0.05) 的股票"
            "\n3. 限制最多选 topk=10 只"
            "\n4. 等权重分配仓位"
            "\n风险控制：数据缺失或NaN时跳过该股票，无符合条件时返回空信号"
        ),
    },
    "momentum": {
        "strategy_name": "MomentumStrategy",
        "description": (
            "动量策略：买入近期涨幅最大的股票。"
            "\n核心逻辑："
            "\n1. 获取股票池(stock_list参数)中每只股票近 N 日收盘价"
            "\n2. 计算过去 20 日收益率（动量分数）"
            "\n3. 按动量排序，选前 topk 只"
            "\n4. 等权重配置仓位"
        ),
    },
    "low_pe": {
        "strategy_name": "LowPEStrategy",
        "description": (
            "低估值策略：选择 PE 低于阈值且为正的股票。"
            "\n核心逻辑："
            "\n1. 获取股票池(stock_list参数)中每只股票 $pe 指标"
            "\n2. 筛选 0 < PE < 30 的股票"
            "\n3. 限制最多选 topk 只"
            "\n4. 等权重配置仓位"
        ),
    },
}

# 已验证可用的示例代码（等权重策略，不依赖数据筛选，确保产生交易信号）
KNOWN_GOOD_CODE = """\
from typing import List
import pandas as pd
from qlib.data import D


class EqualWeightStrategy:
    def __init__(self, stock_list: List[str] = None, topk: int = 10):
        self.stock_list = stock_list or []
        self.topk = topk

    def generate_signals(self, date: str) -> pd.Series:
        if not self.stock_list:
            return pd.Series({})
        stocks = self.stock_list[:self.topk]
        weight = 1.0 / len(stocks)
        return pd.Series({s: weight for s in stocks})
"""


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def context() -> RuntimeContext:
    """创建真实运行时上下文"""
    ctx = create_runtime_context()
    try:
        ctx.knowledge_service.initialize()
    except Exception as e:
        pytest.skip(f"知识库初始化失败: {e}")
    return ctx


@pytest.fixture(scope="module")
def develop_agent(context: RuntimeContext) -> StrategyDevelopAgent:
    """策略开发 Agent（使用真实 LLM）"""
    return StrategyDevelopAgent(context=context)


@pytest.fixture(scope="module")
def backtest_dates(context: RuntimeContext) -> tuple[str, str]:
    """回测日期范围"""
    return context.config.backtest_start_date, context.config.backtest_end_date


# ── 辅助函数 ─────────────────────────────────────────────────────────────


def assert_valid_strategy_code(code: str) -> None:
    """断言代码符合回测接口要求"""
    assert isinstance(code, str), "代码应为字符串"
    assert len(code) > 0, "代码不应为空"
    assert "generate_signals" in code, "代码应包含 generate_signals 方法"
    assert "pd.Series" in code, "代码应返回 pd.Series"
    for char in ["，", "（", "）", "：", "；", "。"]:
        assert char not in code, f"代码不应包含全角字符: {char}"


def assert_backtest_success(result: dict) -> None:
    """断言回测成功并返回完整结果"""
    if "error" in result:
        pytest.fail(f"回测失败: {result['error']}")
    for key in ["total_return", "sharpe_ratio", "max_drawdown", "trading_days"]:
        assert key in result, f"回测结果缺少字段: {key}"


def run_backtest_via_context(
    context: RuntimeContext,
    code: str,
    start_date: str = "",
    end_date: str = "",
    stock_list: list[str] | None = None,
) -> dict:
    """通过 RuntimeContext 的 backtest_service 执行回测"""
    return context.backtest_service.run_backtest(
        strategy_code=code,
        start_date=start_date or context.config.backtest_start_date,
        end_date=end_date or context.config.backtest_end_date,
        stock_list=stock_list,
    )


def print_backtest_result(result: dict) -> None:
    """打印回测结果"""
    if not result.get("error"):
        print(f"  总收益率: {result.get('total_return', 0)}")
        print(f"  年化收益: {result.get('annual_return', 0)}")
        print(f"  夏普比率: {result.get('sharpe_ratio', 0)}")
        print(f"  最大回撤: {result.get('max_drawdown', 0)}")
        print(f"  交易天数: {result.get('trading_days', 0)}")
    else:
        print(f"  错误: {result.get('error', '未知')}")
        print(f"  错误分类: {result.get('error_category', '未知')}")


def refine_and_backtest(
    agent: StrategyDevelopAgent,
    strategy: dict,
    code: str,
    error_msg: str,
    context: RuntimeContext,
    stock_list: list[str] | None = None,
    max_refines: int = 3,
) -> tuple[str, dict]:
    """修复循环：最多 max_refines 轮"""
    for i in range(1, max_refines + 1):
        print(f"\n  --- 修复第 {i} 次 ---")
        code = agent.refine_code(
            strategy=strategy,
            error_message=error_msg,
            failed_code=code,
        )
        print(f"  修复后代码长度: {len(code)} 字符")
        print(f"  代码预览: {code[:120]}...")

        result = run_backtest_via_context(context, code, stock_list=stock_list)
        print_backtest_result(result)

        if not result.get("error"):
            return code, result

        error_msg = result.get("error", "Unknown error")

    return code, result


# ── 测试：代码生成 ───────────────────────────────────────────────────────


class TestDevelop:
    """策略代码生成测试：真实 LLM + mock 策略描述"""

    @pytest.mark.parametrize(
        "strategy_key",
        ["profit_growth", "momentum", "low_pe"],
        ids=["利润增长", "动量", "低估值"],
    )
    def test_develop_generates_valid_code(
        self, develop_agent: StrategyDevelopAgent, strategy_key: str
    ):
        """LLM 应根据策略描述生成符合回测接口的代码"""
        strategy = MOCK_STRATEGIES[strategy_key]
        print(f"\n策略: {strategy['strategy_name']}")

        code = develop_agent.develop_strategy(strategy)

        print(f"代码长度: {len(code)} 字符")
        print(f"代码预览:\n{code[:300]}...")
        assert_valid_strategy_code(code)

    def test_sanitize_code_removes_fullwidth(self):
        """sanitize_code 应清除全角字符（含句号）"""
        dirty = "x = [1，2（3）]  # 注释：测试。"
        clean = sanitize_code(dirty)
        assert "，" not in clean
        assert "（" not in clean
        assert "）" not in clean
        assert "。" not in clean
        assert clean == "x = [1,2(3)]  # 注释:测试."


# ── 测试：回测服务 ───────────────────────────────────────────────────────


class TestBacktest:
    """回测服务测试：直接调用 backtest_service"""

    def test_backtest_known_good_code(
        self, context: RuntimeContext, backtest_dates: tuple[str, str]
    ):
        """用已知可用代码测试回测服务正常执行"""
        start, end = backtest_dates
        print("\n使用已知可用代码测试回测...")
        result = run_backtest_via_context(
            context, KNOWN_GOOD_CODE, start, end, stock_list=DEFAULT_STOCK_LIST
        )
        print_backtest_result(result)
        assert_backtest_success(result)

    def test_backtest_detects_syntax_error(
        self, context: RuntimeContext, backtest_dates: tuple[str, str]
    ):
        """回测服务应检测到语法错误并返回错误信息"""
        start, end = backtest_dates
        result = run_backtest_via_context(
            context, "x = [1，2]", start, end, stock_list=DEFAULT_STOCK_LIST
        )
        assert result is not None, "语法错误应返回错误结果，而非 None"
        assert "error" in result, "语法错误应在 result.error 中反映"

    def test_health_check(self):
        """check_service_health 应返回布尔值"""
        result = check_service_health()
        assert isinstance(result, bool)


# ── 测试：端到端 develop → backtest ──────────────────────────────────────


class TestDevelopAndBacktest:
    """端到端测试：LLM 生成代码 → 回测 → （失败则修复 → 再回测）"""

    @pytest.mark.parametrize(
        "strategy_key",
        ["momentum"],
        ids=["动量"],
    )
    def test_develop_then_backtest(
        self,
        develop_agent: StrategyDevelopAgent,
        strategy_key: str,
        context: RuntimeContext,
        backtest_dates: tuple[str, str],
    ):
        """LLM 生成代码 → 回测 → 失败则修复循环

        验证 develop + backtest 端到端流程：
        - 代码生成应合法（有 generate_signals、pd.Series、无全角字符）
        - 回测应能执行（不应有 code_logic 语法错误）
        - 策略逻辑错误（如无信号）属于数据/参数问题，不视为流程失败
        """
        strategy = MOCK_STRATEGIES[strategy_key]
        name = strategy["strategy_name"]
        start, end = backtest_dates
        print(f"\n策略: {name}")

        # 生成代码
        code = develop_agent.develop_strategy(strategy)
        print(f"生成代码 ({len(code)} 字符)")
        assert_valid_strategy_code(code)

        # 首次回测
        print("\n--- 首次回测 ---")
        result = run_backtest_via_context(
            context, code, start, end, stock_list=DEFAULT_STOCK_LIST
        )
        print_backtest_result(result)

        # 首次回测成功
        if not result.get("error"):
            assert_backtest_success(result)
            print("首次回测即成功!")
            return

        # 区分错误类型
        error_msg = result.get("error", "Unknown error")
        is_code_error = result.get("error_category") == "code_logic"

        if is_code_error:
            # 代码错误需要修复
            print(f"\n代码错误，开始修复: {error_msg[:80]}")
            final_code, final_result = refine_and_backtest(
                develop_agent,
                strategy,
                code,
                error_msg,
                context,
                stock_list=DEFAULT_STOCK_LIST,
                max_refines=3,
            )

            if not final_result.get("error"):
                assert_backtest_success(final_result)
                print("修复后回测成功!")
            elif final_result.get("error_category") == "code_logic":
                print(f"\n修复后仍有代码错误:\n{final_code}")
                pytest.fail(f"代码错误3次修复后仍未解决: {final_result.get('error')}")
            else:
                # 策略逻辑错误（如无信号）—— 流程正确但数据不匹配
                print("修复后产生策略逻辑错误（非代码错误），流程验证通过")
        else:
            # 策略逻辑错误（如无信号）—— 流程正确但数据/参数不匹配
            print(f"\n策略逻辑错误（非代码错误），流程验证通过: {error_msg[:80]}")
