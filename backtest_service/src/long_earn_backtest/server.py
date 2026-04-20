"""回测服务 API 模块"""

import bisect
import logging
import os
import re
import tempfile
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from qlib import init as qlib_init
from qlib.data import D

# 配置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# 中文全角标点到半角标点的映射
_FULLWIDTH_PUNCTUATION_MAP: dict[str, str] = {
    "，": ",",
    "。": ".",
    "；": ";",
    "：": ":",
    "（": "(",
    "）": ")",
    "【": "[",
    "】": "]",
    "｛": "{",
    "｝": "}",
    "！": "!",
    "？": "?",
    "＋": "+",
    "－": "-",
    "＊": "*",
    "／": "/",
    "＝": "=",
    "＠": "@",
    "＃": "#",
    "＄": "$",
    "％": "%",
    "＾": "^",
    "＆": "&",
    "～": "~",
    "\u201c": '"',  # LEFT DOUBLE QUOTATION MARK → "
    "\u201d": '"',  # RIGHT DOUBLE QUOTATION MARK → "
    "\u2018": "'",  # LEFT SINGLE QUOTATION MARK → '
    "\u2019": "'",  # RIGHT SINGLE QUOTATION MARK → '
}

_FULLWIDTH_PATTERN = re.compile(
    "[" + "".join(re.escape(c) for c in _FULLWIDTH_PUNCTUATION_MAP) + "]"
)

# 非代码行检测模式：检测中文自然语言段落（非注释、非字符串内的中文描述）
_NON_CODE_LINE_PATTERN = re.compile(r"^[^#\s\"']*[\u4e00-\u9fff]", re.UNICODE)

# 初始化 qlib
qlib_data_path = Path.home() / ".qlib_data" / "cn_data"
if qlib_data_path.exists():
    qlib_init(provider_uri=str(qlib_data_path), region="cn")
    logger.info(f"qlib 数据路径：{qlib_data_path}")
else:
    qlib_init(region="cn")
    logger.warning("qlib 数据路径不存在，使用默认配置")

logger.info("qlib 初始化成功")

app = FastAPI(title="Long Earn Backtest Service", version="0.1.0")


class BacktestRequest(BaseModel):
    """回测请求模型"""

    strategy_code: str
    start_date: str = "2020-01-01"
    end_date: str = "2023-12-31"
    stock_list: list | None = None


class BacktestResponse(BaseModel):
    """回测响应模型"""

    success: bool
    message: str
    error_category: str | None = None  # "code_logic" 或 "strategy_logic"
    error_detail: str | None = None  # 精准错误原因
    total_return: float | None = None
    annual_return: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown: float | None = None
    win_rate: float | None = None
    trading_days: int | None = None


def _validate_code(code: str) -> tuple[str | None, str | None]:
    """预检策略代码，返回 (error_category, error_detail) 或 (None, None) 表示通过。

    检查项：
    1. 中文全角标点（常见 LLM 生成错误）
    2. 中文自然语言混入代码（非注释/字符串的中文描述行）
    3. Python 语法编译检查
    """
    # 检查全角标点
    fullwidth_matches = list(_FULLWIDTH_PATTERN.finditer(code))
    if fullwidth_matches:
        # 找到第一个全角标点所在行
        first_match = fullwidth_matches[0]
        char = first_match.group()
        line_no = code[: first_match.start()].count("\n") + 1
        halfwidth = _FULLWIDTH_PUNCTUATION_MAP.get(char, "?")
        hint_lines: list[str] = []
        for m in fullwidth_matches[:5]:  # 最多显示 5 处
            c = m.group()
            ln = code[: m.start()].count("\n") + 1
            hw = _FULLWIDTH_PUNCTUATION_MAP.get(c, "?")
            hint_lines.append(f"  第 {ln} 行：'{c}' (U+{ord(c):04X}) → 应为 '{hw}'")
        detail = (
            f"代码包含中文全角标点，Python 无法识别。\n"
            f"首个错误：第 {line_no} 行的 '{char}' (U+{ord(char):04X})，应使用半角 '{halfwidth}'\n"
            + "\n".join(hint_lines)
        )
        if len(fullwidth_matches) > 5:
            detail += f"\n  ... 共 {len(fullwidth_matches)} 处全角标点"
        return "code_logic", detail

    # 检查中文自然语言混入代码（非注释、非字符串内）
    non_code_lines: list[tuple[int, str]] = []
    for i, line in enumerate(code.split("\n"), 1):
        stripped = line.strip()
        if not stripped:
            continue
        # 跳过注释行
        if stripped.startswith("#"):
            continue
        # 跳过字符串赋值行（简单判断）
        if (
            '="' in stripped
            or "'=" in stripped
            or '"""' in stripped
            or "'''" in stripped
        ):
            continue
        if _NON_CODE_LINE_PATTERN.match(stripped):
            non_code_lines.append((i, stripped[:80]))

    if non_code_lines:
        lines_info = "\n".join(
            f"  第 {ln} 行：{content}" for ln, content in non_code_lines[:5]
        )
        detail = (
            "代码包含中文自然语言描述（非 Python 语句），疑似 LLM 未正确输出纯代码。\n"
            + lines_info
        )
        if len(non_code_lines) > 5:
            detail += f"\n  ... 共 {len(non_code_lines)} 处非代码行"
        return "code_logic", detail

    # 语法编译检查
    try:
        compile(code, "<strategy_code>", "exec")
    except SyntaxError as e:
        detail = f"Python 语法错误：第 {e.lineno} 行"
        if e.text:
            detail += f"\n  {e.text.strip()}"
        if e.msg:
            detail += f"\n  错误信息：{e.msg}"
        detail += "\n请检查策略代码的语法是否正确。"
        return "code_logic", detail

    return None, None


@app.post("/api/v1/backtest", response_model=BacktestResponse)
async def run_backtest(request: BacktestRequest):
    """运行回测"""
    start_time = pd.Timestamp.now()
    logger.info(
        f"收到回测请求：start_date={request.start_date}, end_date={request.end_date}, stocks={len(request.stock_list or [])}"
    )

    try:
        # 代码预检
        cat, detail = _validate_code(request.strategy_code)
        if cat is not None:
            logger.warning(f"代码预检未通过：{detail}")
            return BacktestResponse(
                success=False,
                message="策略代码存在语法问题",
                error_category=cat,
                error_detail=detail,
            )

        # 创建临时文件
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(request.strategy_code)
            temp_path = f.name

        logger.info(f"策略代码已写入临时文件：{temp_path}")

        try:
            # 导入策略模块
            import importlib.util

            strategy_name = "dynamic_strategy"
            spec = importlib.util.spec_from_file_location(strategy_name, temp_path)
            if spec is None or spec.loader is None:
                error_msg = "无法加载策略模块"
                logger.error(error_msg)
                return BacktestResponse(
                    success=False,
                    message=error_msg,
                    error_category="code_logic",
                    error_detail="importlib 无法从策略文件创建模块规范，请检查文件路径和格式。",
                )

            strategy_module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(strategy_module)
            except SyntaxError as e:
                detail = f"Python 语法错误：第 {e.lineno} 行"
                if e.text:
                    detail += f"\n  {e.text.strip()}"
                if e.msg:
                    detail += f"\n  错误信息：{e.msg}"
                # 检查是否为全角标点导致的
                if e.text and _FULLWIDTH_PATTERN.search(e.text):
                    detail += (
                        "\n原因：代码中包含中文全角标点，请将全角标点替换为半角标点。"
                    )
                logger.error(detail)
                return BacktestResponse(
                    success=False,
                    message="策略代码存在语法错误",
                    error_category="code_logic",
                    error_detail=detail,
                )
            except ImportError as e:
                detail = f"导入错误：{e}"
                logger.error(detail)
                return BacktestResponse(
                    success=False,
                    message="策略代码导入依赖失败",
                    error_category="code_logic",
                    error_detail=f"策略代码引用了不存在的模块或包：{e}\n请检查 import 语句是否正确。",
                )
            logger.info("策略模块加载成功")

            # 查找策略类
            strategy_class = None
            for name, obj in strategy_module.__dict__.items():
                if hasattr(obj, "generate_signals"):
                    strategy_class = obj
                    logger.info(f"找到策略类：{name}")
                    break

            if not strategy_class:
                error_msg = "策略类未找到"
                logger.error(error_msg)
                return BacktestResponse(
                    success=False,
                    message=error_msg,
                    error_category="code_logic",
                    error_detail="策略代码中未定义包含 generate_signals 方法的类。"
                    "请确保策略类包含 generate_signals(self, date: str) 方法。",
                )

            # 创建策略实例
            import inspect

            try:
                sig = inspect.signature(strategy_class.__init__)
                if "stock_list" in sig.parameters:
                    strategy = strategy_class(stock_list=request.stock_list)
                    logger.info(
                        f"策略实例已创建，传入 stock_list: {request.stock_list}"
                    )
                else:
                    strategy = strategy_class()
                    logger.info("策略实例已创建（无 stock_list 参数）")
            except (TypeError, AttributeError) as e:
                detail = f"策略类实例化失败：{e}"
                logger.error(detail)
                return BacktestResponse(
                    success=False,
                    message="策略类实例化失败",
                    error_category="code_logic",
                    error_detail=f"创建策略实例时出错：{e}\n请检查 __init__ 方法的参数签名和默认值。",
                )

            # 获取交易日历
            logger.info(f"获取交易日历：{request.start_date} 至 {request.end_date}")
            dates = D.calendar(
                start_time=request.start_date, end_time=request.end_date, freq="day"
            )
            dates = pd.to_datetime(dates)
            dates_list = dates.tolist()  # 转换为列表避免死循环
            logger.info(f"获取到 {len(dates_list)} 个交易日")

            if len(dates_list) == 0:
                logger.warning("交易日历为空")
                return BacktestResponse(
                    success=False,
                    message="交易日历为空，请检查日期范围",
                    error_category="strategy_logic",
                    error_detail=f"日期范围 {request.start_date} 至 {request.end_date} 内无交易日。"
                    "请确认日期格式（YYYY-MM-DD）和范围是否正确。",
                )

            # 预加载收盘价数据（一次性查询替代逐日查询）
            price_cache = _preload_close_prices(
                request.stock_list, request.start_date, request.end_date
            )

            # 执行回测
            daily_returns = []
            error_count = 0
            empty_signal_count = 0
            signal_errors: list[str] = []  # 记录信号生成错误信息

            logger.info("开始执行回测...")
            for i, date in enumerate(dates_list):
                date_str = date.strftime("%Y-%m-%d")

                # 每 50 个交易日记录一次进度
                if (i + 1) % 50 == 0:
                    logger.info(f"回测进度：{i + 1}/{len(dates_list)}")

                try:
                    signals = strategy.generate_signals(date_str)
                except Exception as e:
                    error_msg = f"{date_str}: {e}"
                    logger.error(f"生成信号失败 ({date_str}): {e}")
                    signal_errors.append(error_msg)
                    error_count += 1
                    if error_count > len(dates_list) * 0.5:  # 超过 50% 失败则停止
                        logger.error("过多交易日生成信号失败，终止回测")
                        break
                    continue

                if signals is None:
                    empty_signal_count += 1
                    continue

                if hasattr(signals, "__len__") and len(signals) == 0:
                    empty_signal_count += 1
                    continue

                try:
                    portfolio_return = _get_portfolio_return(
                        signals, date_str, price_cache
                    )
                    if portfolio_return is not None:
                        daily_returns.append(portfolio_return)
                except Exception as e:
                    logger.error(f"计算组合收益失败 ({date_str}): {e}")
                    error_count += 1
                    raise

            logger.info(
                f"回测完成，有效交易日：{len(daily_returns)}, 错误数：{error_count}, 空信号数：{empty_signal_count}"
            )

            if not daily_returns:
                # 根据错误类型分类
                if error_count > empty_signal_count:
                    # 主要是 generate_signals 抛异常
                    detail = (
                        f"共 {len(dates_list)} 个交易日中，{error_count} 个生成信号失败，"
                        f"{empty_signal_count} 个返回空信号。\n"
                        "常见原因：\n"
                        "  1. generate_signals 方法内部访问了不存在的数据字段\n"
                        "  2. 策略逻辑对某些日期的数据格式做了错误假设\n"
                        f"前几条错误：\n"
                        + "\n".join(f"  - {e}" for e in signal_errors[:5])
                    )
                    return BacktestResponse(
                        success=False,
                        message="策略 generate_signals 方法执行异常",
                        error_category="strategy_logic",
                        error_detail=detail,
                    )
                else:
                    # 主要是空信号
                    detail = (
                        f"共 {len(dates_list)} 个交易日中，{empty_signal_count} 个返回空信号（None 或空字典），"
                        f"{error_count} 个执行出错。\n"
                        "常见原因：\n"
                        "  1. 策略的选股/择时条件过于严格，所有日期均不满足买入条件\n"
                        "  2. 策略内部使用的股票代码格式与 qlib 数据不匹配"
                        "（qlib 使用 SH600000 格式）\n"
                        "  3. 策略未正确返回 signals 字典（应返回 dict[str, float]，"
                        "如 {'SH600000': 0.5}）\n"
                        "  4. 数据获取范围不覆盖策略所需的历史窗口"
                    )
                    return BacktestResponse(
                        success=False,
                        message="策略未产生任何有效交易信号",
                        error_category="strategy_logic",
                        error_detail=detail,
                    )

            # 计算指标
            returns_series = pd.Series(daily_returns)
            total_return = (1 + returns_series).cumprod().iloc[-1] - 1
            sharpe_ratio = (
                returns_series.mean() / returns_series.std() * np.sqrt(252)
                if returns_series.std() != 0
                else 0
            )
            cumulative = (1 + returns_series).cumprod()
            max_drawdown = (
                cumulative.cummax() - cumulative
            ).max() / cumulative.cummax().max()

            elapsed_time = (pd.Timestamp.now() - start_time).total_seconds()
            logger.info(f"回测成功完成，耗时：{elapsed_time:.2f}秒")

            return BacktestResponse(
                success=True,
                message="回测成功",
                total_return=float(total_return),
                annual_return=float(
                    (1 + total_return) ** (252 / len(daily_returns)) - 1
                ),
                sharpe_ratio=float(sharpe_ratio),
                max_drawdown=float(max_drawdown),
                win_rate=float((returns_series > 0).sum() / len(returns_series)),
                trading_days=len(daily_returns),
            )

        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
                logger.debug(f"临时文件已清理：{temp_path}")

    except HTTPException:
        raise
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"回测失败：{e}\n{error_details}")
        raise HTTPException(status_code=500, detail=str(e)) from e


def _preload_close_prices(
    stock_list: list[str] | None,
    start_date: str,
    end_date: str,
) -> dict[str, tuple[list[str], dict[str, float]]]:
    """预加载收盘价数据，构建快速查找缓存

    一次性 D.features 调用替代逐日查询，预计加速 100x+。

    Args:
        stock_list: 股票池列表
        start_date: 开始日期
        end_date: 结束日期

    Returns:
        缓存字典：stock_code -> (sorted_dates, date_to_price)
    """
    cache: dict[str, tuple[list[str], dict[str, float]]] = {}
    if not stock_list:
        return cache

    try:
        logger.info(f"预加载收盘价：{len(stock_list)} 只股票, {start_date} ~ {end_date}")
        close_data = D.features(
            stock_list, ["$close"],
            start_time=start_date, end_time=end_date,
        )
        if close_data is None or close_data.empty:
            logger.warning("预加载收盘价数据为空")
            return cache

        # 构建 stock -> (sorted_dates, {date: close}) 的快速查找结构
        for stock in stock_list:
            try:
                stock_series = close_data.loc[(stock, slice(None)), "$close"]
                date_prices: dict[str, float] = {}
                for dt, price in stock_series.items():
                    date_prices[dt.strftime("%Y-%m-%d")] = float(price)
                if date_prices:
                    cache[stock] = (sorted(date_prices.keys()), date_prices)
            except KeyError:
                continue

        logger.info(f"预加载完成：{len(cache)}/{len(stock_list)} 只股票有数据")
    except Exception as e:
        logger.warning(f"预加载收盘价失败：{e}，将逐日查询")

    return cache


def _get_portfolio_return(
    signals,
    date_str: str,
    price_cache: dict[str, tuple[list[str], dict[str, float]]] | None = None,
) -> float:
    """获取组合收益率

    优先使用预加载的价格缓存（bisect 二分查找前一日），回退到逐日查询。

    Args:
        signals: 交易信号 dict{stock: weight}
        date_str: 当前日期
        price_cache: 预加载的收盘价缓存
    """
    if signals is None or len(signals) == 0:
        return 0.0

    stock_list = list(signals.keys())
    if len(stock_list) == 0:
        return 0.0

    try:
        portfolio_return = 0.0
        total_weight = 0.0

        for stock, weight in signals.items():
            if weight == 0:
                continue

            close_today: float | None = None
            close_prev: float | None = None

            if price_cache and stock in price_cache:
                # 走缓存：bisect 二分查找前一个交易日，O(log n)
                sorted_dates, date_prices = price_cache[stock]
                close_today = date_prices.get(date_str)
                if close_today is not None:
                    idx = bisect.bisect_left(sorted_dates, date_str)
                    if idx > 0:
                        close_prev = date_prices[sorted_dates[idx - 1]]
            else:
                # 回退：逐日查询（兼容无缓存场景）
                end_date = pd.Timestamp(date_str)
                start_date = end_date - pd.Timedelta(days=10)
                close_data = D.features(
                    [stock], ["$close"],
                    start_time=start_date, end_time=end_date,
                )
                if close_data is not None and not close_data.empty:
                    stock_data = close_data.loc[(stock, slice(None)), "$close"]
                    if len(stock_data) >= 2:
                        close_today = float(stock_data.iloc[-1])
                        close_prev = float(stock_data.iloc[-2])

            if close_today is not None and close_prev is not None and close_prev > 0:
                stock_return = (close_today - close_prev) / close_prev
                portfolio_return += weight * stock_return
                total_weight += abs(weight)

        if total_weight > 0:
            portfolio_return /= total_weight

        return portfolio_return

    except Exception as e:
        logger.error(f"获取数据失败 ({date_str}): {e}")
        raise


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy", "qlib": "initialized"}
