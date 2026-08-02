"""标准输出编码初始化。

Windows 默认控制台为 CP936，Python stdout/stderr 亦常为 gbk；
Cursor / 现代终端按 UTF-8 捕获时会出现中文乱码。入口处尽早将
控制台与 stdio 切到 UTF-8。
"""

from __future__ import annotations

import contextlib
import ctypes
import sys


def ensure_utf8_stdio() -> None:
    """将 Windows 控制台与 stdout/stderr 切换为 UTF-8（幂等）。"""
    if sys.platform == "win32":
        with contextlib.suppress(AttributeError, OSError):
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        with contextlib.suppress(OSError, ValueError, AttributeError):
            reconfigure(encoding="utf-8", errors="replace")
