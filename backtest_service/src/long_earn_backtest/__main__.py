"""回测服务主模块"""

import os
from pathlib import Path

if __name__ == "__main__":
    import uvicorn

    # 优先使用 Unix Domain Socket（零 TCP 开销，无端口冲突）
    uds = os.getenv("BACKTEST_SERVICE_UDS", "")
    if uds:
        Path(uds).parent.mkdir(parents=True, exist_ok=True)
        uvicorn.run(
            "long_earn_backtest.server:app",
            uds=uds,
            reload=True,
            reload_dirs=["src/long_earn_backtest"],
        )
    else:
        uvicorn.run(
            "long_earn_backtest.server:app",
            host="0.0.0.0",
            port=int(os.getenv("BACKTEST_SERVICE_PORT", "8001")),
            reload=True,
            reload_dirs=["src/long_earn_backtest"],
        )
