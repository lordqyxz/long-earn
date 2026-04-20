"""回测服务主模块"""

from long_earn_backtest.server import app

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "long_earn_backtest.server:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
        reload_dirs=["src/long_earn_backtest"],
    )
