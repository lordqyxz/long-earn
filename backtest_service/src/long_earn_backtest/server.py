"""回测服务 API 模块"""

import logging
import tempfile
import os
import traceback
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from qlib import init as qlib_init
from qlib.data import D

# 配置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

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
    stock_list: Optional[list] = None


class BacktestResponse(BaseModel):
    """回测响应模型"""

    success: bool
    message: str
    total_return: Optional[float] = None
    annual_return: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    max_drawdown: Optional[float] = None
    win_rate: Optional[float] = None
    trading_days: Optional[int] = None


@app.post("/api/v1/backtest", response_model=BacktestResponse)
async def run_backtest(request: BacktestRequest):
    """运行回测"""
    start_time = pd.Timestamp.now()
    logger.info(
        f"收到回测请求：start_date={request.start_date}, end_date={request.end_date}, stocks={len(request.stock_list or [])}"
    )

    try:
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
                raise HTTPException(status_code=400, detail=error_msg)

            strategy_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(strategy_module)
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
                raise HTTPException(status_code=400, detail=error_msg)

            # 创建策略实例
            import inspect

            sig = inspect.signature(strategy_class.__init__)
            if "stock_list" in sig.parameters:
                strategy = strategy_class(stock_list=request.stock_list)
                logger.info(f"策略实例已创建，传入 stock_list: {request.stock_list}")
            else:
                strategy = strategy_class()
                logger.info("策略实例已创建（无 stock_list 参数）")

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
                    success=False, message="交易日历为空，请检查日期范围"
                )

            # 执行回测
            daily_returns = []
            error_count = 0
            empty_signal_count = 0

            logger.info("开始执行回测...")
            for i, date in enumerate(dates_list):
                date_str = date.strftime("%Y-%m-%d")

                # 每 10 个交易日记录一次进度
                if (i + 1) % 10 == 0:
                    logger.info(f"回测进度：{i+1}/{len(dates_list)}")

                try:
                    signals = strategy.generate_signals(date_str)
                except Exception as e:
                    logger.error(f"生成信号失败 ({date_str}): {e}")
                    error_count += 1
                    if error_count > len(dates_list) * 0.5:  # 超过 50% 失败则停止
                        logger.error("过多交易日生成信号失败，终止回测")
                        break
                    continue

                if signals is None:
                    logger.warning(f"信号为 None ({date_str})")
                    empty_signal_count += 1
                    continue

                if hasattr(signals, "__len__") and len(signals) == 0:
                    logger.warning(f"信号为空 ({date_str})")
                    empty_signal_count += 1
                    continue

                try:
                    portfolio_return = _get_portfolio_return(signals, date_str)
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
                logger.warning("没有有效的回测数据")
                return BacktestResponse(success=False, message="没有有效的回测数据")

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
        raise HTTPException(status_code=500, detail=str(e))


def _get_portfolio_return(signals, date_str: str) -> float:
    """获取组合收益率"""
    if signals is None or len(signals) == 0:
        return 0.0

    stock_list = list(signals.keys())
    if len(stock_list) == 0:
        return 0.0

    try:
        end_date = pd.Timestamp(date_str)
        start_date = end_date - pd.Timedelta(days=10)

        logger.debug(
            f"获取数据：{stock_list}, {start_date.date()} 至 {end_date.date()}"
        )

        close_data = D.features(
            stock_list, ["$close"], start_time=start_date, end_time=end_date
        )

        if close_data is None or close_data.empty:
            logger.warning(f"无数据可用 ({date_str})")
            return 0.0

        portfolio_return = 0.0
        total_weight = 0.0

        for stock, weight in signals.items():
            if weight == 0:
                continue

            try:
                # qlib 返回的 DataFrame 索引为 (instrument, datetime) 的 MultiIndex
                stock_data = close_data.loc[(stock, slice(None)), "$close"]

                if len(stock_data) >= 2:
                    latest_close = stock_data.iloc[-1]
                    prev_close = stock_data.iloc[-2]

                    if prev_close > 0:
                        stock_return = (latest_close - prev_close) / prev_close
                        portfolio_return += weight * stock_return
                        total_weight += abs(weight)
            except (KeyError, IndexError) as e:
                logger.debug(f"获取 {stock} 数据失败：{e}")
                continue

        if total_weight > 0:
            portfolio_return /= total_weight

        return portfolio_return

    except Exception as e:
        logger.error(f"获取数据失败 ({date_str}): {e}")
        raise  # 重新抛出异常以便上层捕获


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy", "qlib": "initialized"}
