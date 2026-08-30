"""内存映射 Arrow IPC 文件共享数据底座

主进程将 pl.DataFrame 写为未压缩 Arrow IPC 临时文件，worker 进程
``pl.read_ipc(path, memory_map=True)`` 内存映射读取：物理页由 OS 页缓存
统一承载，所有进程共享同一份页面，worker 私有内存占用趋近于零。

替代旧 SharedMemory + BytesIO 方案：旧方案 worker attach 时
``bytes(shm.buf)`` 复制整块 + ``read_ipc`` 再物化一份私有 DataFrame，
N 个 worker 各持 2 份面板拷贝，大股票池网格下内存放大 N×2 倍
（叠加引擎侧 filter/sort 复制可达 N×3 以上）。

生命周期由主进程统一管理：try/finally + atexit 兜底删除临时文件。
"""

from __future__ import annotations

import atexit
import os
import tempfile
from typing import Any

import polars as pl
from loguru import logger

from long_earn.core.storage import get_data_dir


class SharedDataContext:
    """mmap Arrow IPC 文件共享 polars DataFrame 的上下文管理器。

    主进程创建：with SharedDataContext(df) as ctx: ...
    worker 进程：df = SharedDataContext.attach(ctx.get_worker_args())

    生命周期由主进程管理；worker 只读映射，无需清理。
    临时文件落盘位置由 core.storage 统一裁决（数据根目录 tmp/ 子目录）。
    """

    def __init__(self, df: pl.DataFrame) -> None:
        self._df = df
        self.path: str = ""

    def __enter__(self) -> SharedDataContext:
        tmp_dir = get_data_dir() / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(suffix=".arrow", dir=tmp_dir)
        os.close(fd)
        try:
            # 未压缩 IPC 才能内存映射；压缩格式 read_ipc 会退化为整块读入
            self._df.write_ipc(path, compression="uncompressed")
        except Exception:
            os.unlink(path)
            raise
        self.path = path
        logger.debug(f"共享面板 IPC 文件: {path}, rows={self._df.height}")
        atexit.register(self._cleanup)
        return self

    def __exit__(self, *args: Any) -> None:
        # 注销 atexit 钩子：长进程（research loop）多次 run_grid 会累积
        # handler，每个 handler 持有 self 阻止对象回收
        atexit.unregister(self._cleanup)
        self._cleanup()

    def _cleanup(self) -> None:
        if not self.path:
            return
        try:
            os.unlink(self.path)
        except OSError as e:
            logger.warning(f"共享面板临时文件删除异常: {e}")
        self.path = ""

    @staticmethod
    def attach(path: str) -> pl.DataFrame:
        """worker 端内存映射恢复 DataFrame（零拷贝共享 OS 页缓存）。"""
        return pl.read_ipc(path, memory_map=True)

    def get_worker_args(self) -> str:
        """获取传递给 worker 的面板文件路径。"""
        return self.path
