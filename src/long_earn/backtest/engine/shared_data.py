"""SharedMemory + Arrow IPC 共享数据底座

主进程将 pl.DataFrame 写入 SharedMemory（Arrow IPC 格式），worker 进程通过 attach 读取。
生命周期由主进程统一管理：try/finally + atexit 兜底 unlink。
当 SharedMemory 不可用时退化为 pickle 路径。
"""

from __future__ import annotations

import atexit
import io
import pickle
from multiprocessing import shared_memory
from typing import Any

import polars as pl
from loguru import logger

_MAX_GRID_SHARED_MEMORY = 2 * 1024 * 1024 * 1024  # 2GB 安全上限


class SharedDataContext:
    """SharedMemory 共享 polars DataFrame 的上下文管理器。

    主进程创建：with SharedDataContext(df) as ctx: ...
    worker 进程：df = SharedDataContext.attach(ctx.token)

    生命周期由主进程管理；worker 只 attach 不 close。
    当 SharedMemory 不可用时退化为 pickle 路径。
    """

    def __init__(self, df: pl.DataFrame) -> None:
        self._df = df
        self._shm: shared_memory.SharedMemory | None = None
        self.token: str = ""
        self.size: int = 0
        self._pickle_fallback: bytes = b""
        self._use_pickle = False

    def __enter__(self) -> SharedDataContext:
        try:
            buf = io.BytesIO()
            self._df.write_ipc(buf)
            data = buf.getvalue()

            if len(data) > _MAX_GRID_SHARED_MEMORY:
                raise MemoryError(
                    f"数据 {len(data)} bytes 超过共享内存上限 {_MAX_GRID_SHARED_MEMORY}"
                )

            self._shm = shared_memory.SharedMemory(create=True, size=len(data))
            self._shm.buf[: len(data)] = data
            self.token = self._shm.name
            self.size = len(data)
            logger.debug(f"SharedMemory 创建: name={self.token}, size={self.size}")
            atexit.register(self._cleanup)
        except Exception as e:
            logger.warning(f"SharedMemory 不可用，退化为 pickle 路径: {e}")
            self._pickle_fallback = pickle.dumps(self._df)
            self._use_pickle = True
            self.token = "pickle"
        return self

    def __exit__(self, *args: Any) -> None:
        self._cleanup()

    def _cleanup(self) -> None:
        if self._shm is not None:
            try:
                self._shm.close()
                self._shm.unlink()
            except Exception as e:
                logger.warning(f"SharedMemory 清理异常: {e}")
            self._shm = None

    @staticmethod
    def attach(token: str, size: int = 0, pickle_data: bytes = b"") -> pl.DataFrame:
        """worker 端从 token 恢复 DataFrame。

        Args:
            token: SharedMemory name 或 "pickle"
            size: 数据大小（SharedMemory 模式）
            pickle_data: pickle 模式的序列化数据
        """
        if token == "pickle":
            return pickle.loads(pickle_data)
        shm = shared_memory.SharedMemory(name=token, create=False)
        try:
            buf = io.BytesIO(bytes(shm.buf[:size]))
            return pl.read_ipc(buf)
        finally:
            shm.close()

    def get_worker_args(self) -> tuple[str, int, bytes]:
        """获取传递给 worker 的参数 (token, size, pickle_data)。"""
        if self._use_pickle:
            return ("pickle", 0, self._pickle_fallback)
        return (self.token, self.size, b"")
