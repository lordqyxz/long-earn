"""服务管理器实现

提供本地回测服务的生命周期管理（启动/停止/健康检查）。
远程部署场景使用空实现（RemoteServiceManager）。
"""

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from long_earn.services import ServiceManager
from long_earn.tools.backtest import check_service_health

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext

logger = logging.getLogger(__name__)


class LocalServiceManager(ServiceManager):
    """本地服务管理器

    通过 subprocess 启动/停止 backtest_service 子进程。
    支持 Unix Domain Socket 和 TCP 两种模式。
    """

    def __init__(self, context: "RuntimeContext"):
        """初始化本地服务管理器

        Args:
            context: 运行时上下文
        """
        self.context = context
        self._process: subprocess.Popen | None = None
        self._uds_path = os.getenv("BACKTEST_SERVICE_UDS", "")
        self._port = int(os.getenv("BACKTEST_SERVICE_PORT", "8001"))
        self._service_url = context.config.backtest_service_url

    def start(self) -> bool:
        """启动本地回测服务

        Returns:
            是否启动成功
        """
        if self.is_running():
            logger.info("回测服务已在运行")
            return True

        # 清理旧的 UDS socket 文件
        if self._uds_path and Path(self._uds_path).exists():
            Path(self._uds_path).unlink()
            logger.info(f"清理旧 UDS socket: {self._uds_path}")

        # 定位 backtest_service 目录
        repo_root = Path(__file__).resolve().parents[3]
        backtest_dir = repo_root / "backtest_service"
        if not backtest_dir.exists():
            logger.error(f"backtest_service 目录不存在: {backtest_dir}")
            return False

        # 构建启动命令
        uv_exe = shutil.which("uv")
        if uv_exe is None:
            logger.error("未找到 uv 命令，无法启动回测服务")
            return False

        cmd = [uv_exe, "run", "python", "-m", "long_earn_backtest"]
        env = os.environ.copy()
        if self._uds_path:
            env["BACKTEST_SERVICE_UDS"] = self._uds_path
        else:
            env["BACKTEST_SERVICE_PORT"] = str(self._port)

        logger.info(
            f"启动回测服务: {cmd} (uds={self._uds_path or 'N/A'}, port={self._port})"
        )

        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=str(backtest_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception as e:
            logger.error(f"启动回测服务失败: {e}")
            return False

        # 轮询等待服务就绪（最多 10 秒）
        for _ in range(100):
            if self._process.poll() is not None:
                # 进程已退出
                stdout, stderr = self._process.communicate()
                logger.error(
                    f"回测服务启动失败，退出码={self._process.returncode}\n"
                    f"stdout: {stdout.decode()[-500:]}\n"
                    f"stderr: {stderr.decode()[-500:]}"
                )
                self._process = None
                return False

            if check_service_health(self._service_url):
                logger.info("回测服务启动成功")
                return True

            time.sleep(0.1)

        logger.warning("回测服务启动超时，但进程仍在运行")
        return self._process.poll() is None

    def stop(self) -> bool:
        """停止本地回测服务

        Returns:
            是否停止成功
        """
        if self._process is None:
            logger.info("回测服务未在运行")
            return True

        logger.info("停止回测服务...")
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("回测服务终止超时，强制 kill")
            self._process.kill()
            self._process.wait()

        self._process = None

        # 清理 UDS socket 文件
        if self._uds_path and Path(self._uds_path).exists():
            Path(self._uds_path).unlink()
            logger.info(f"清理 UDS socket: {self._uds_path}")

        logger.info("回测服务已停止")
        return True

    def is_running(self) -> bool:
        """检查回测服务是否在运行

        Returns:
            服务是否在运行
        """
        if self._process is not None and self._process.poll() is None:
            return True
        # 即使没有我们启动的进程，也可能有其他实例在运行
        return check_service_health(self._service_url)


class RemoteServiceManager(ServiceManager):
    """远程服务管理器（空实现）

    当回测服务部署在远程时，不提供启停能力。
    is_running() 始终返回 True，表示依赖外部运维。
    """

    def __init__(self, context: "RuntimeContext"):
        """初始化远程服务管理器

        Args:
            context: 运行时上下文
        """
        self.context = context

    def start(self) -> bool:
        """空实现：远程服务由外部运维管理"""
        logger.info("远程回测服务，跳过启动（由外部运维管理）")
        return True

    def stop(self) -> bool:
        """空实现：远程服务由外部运维管理"""
        logger.info("远程回测服务，跳过停止（由外部运维管理）")
        return True

    def is_running(self) -> bool:
        """始终返回 True：假设远程服务可用"""
        return True
