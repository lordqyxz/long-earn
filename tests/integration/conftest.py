"""集成测试 conftest - 自动管理回测服务子进程

回测服务作为子进程启动，日志实时输出到终端。
- 测试开始时自动启动（如端口被占用则先清理旧进程）
- 测试结束后自动清理
- 支持通过 BACKTEST_SERVICE_MANUAL=1 环境变量跳过自动管理
"""

import os
import signal
import subprocess
import time

import pytest
from dotenv import load_dotenv

load_dotenv()

# 回测服务地址（从环境变量读取，与 backtest.py 保持一致）
BACKTEST_SERVICE_URL = os.getenv("BACKTEST_SERVICE_URL", "http://localhost:8001")

# 回测服务项目目录
BACKTEST_SERVICE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "backtest_service",
)

# 最大等待服务启动时间（秒）
STARTUP_TIMEOUT = 30

# 回测服务端口（从 URL 中提取）
_PORT = int(BACKTEST_SERVICE_URL.rstrip("/").split(":")[-1])


def _kill_port_process(port: int) -> None:
    """杀掉占用指定端口的进程"""
    try:
        import subprocess as sp

        pids = sp.check_output(["lsof", "-ti", f":{port}"], stderr=sp.DEVNULL)
        for pid in pids.decode().strip().split("\n"):
            pid = pid.strip()
            if pid:
                os.kill(int(pid), signal.SIGTERM)
    except (subprocess.CalledProcessError, ProcessLookupError, ValueError):
        pass  # 端口未被占用或进程已不存在


@pytest.fixture(scope="session", autouse=True)
def backtest_service():
    """session 级别的回测服务管理

    1. 如果服务已在线，直接复用
    2. 如果端口被占用但服务不在线，先清理旧进程再启动
    3. 以子进程方式启动回测服务，日志实时输出到终端
    4. session 结束后清理子进程
    """
    # 如果服务已在线，直接复用
    from long_earn.tools.backtest import check_service_health

    if check_service_health():
        yield
        return

    # 如果设置手动管理模式，跳过自动启动
    if os.getenv("BACKTEST_SERVICE_MANUAL"):
        pytest.skip("回测服务未启动且设置了手动管理模式")
        return

    # 端口可能被旧进程占用，先清理
    _kill_port_process(_PORT)
    time.sleep(1)

    # 以子进程方式启动回测服务
    print("\n[conftest] 启动回测服务子进程...")
    proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "long_earn_backtest"],
        cwd=BACKTEST_SERVICE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # 等待服务上线
    deadline = time.time() + STARTUP_TIMEOUT
    started = False
    last_output = ""
    while time.time() < deadline:
        # 非阻塞读取日志
        try:
            import fcntl

            fd = proc.stdout.fileno()
            fl = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
            try:
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").rstrip()
                    if text:
                        print(f"[backtest] {text}")
                        last_output = text
            except (OSError, BlockingIOError):
                pass
            finally:
                fcntl.fcntl(fd, fcntl.F_SETFL, fl)
        except ImportError:
            pass  # Windows 没有 fcntl

        if check_service_health():
            started = True
            break

        # 检查进程是否意外退出
        if proc.poll() is not None:
            remaining = proc.stdout.read().decode("utf-8", errors="replace")
            if remaining:
                print(f"[backtest] {remaining}")
            pytest.fail(
                f"回测服务进程意外退出 (exit code={proc.returncode})\n"
                f"最后日志: {last_output}"
            )
            return

        time.sleep(1)

    if not started:
        proc.terminate()
        pytest.fail(f"回测服务在 {STARTUP_TIMEOUT}s 内未上线")

    print(f"[conftest] 回测服务已上线 (PID={proc.pid})")
    yield

    # 清理子进程
    print(f"\n[conftest] 清理回测服务子进程 (PID={proc.pid})...")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    print("[conftest] 回测服务已停止")
