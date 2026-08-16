"""导出 FastAPI OpenAPI schema 到 web/openapi.json（本地 api:gen 用）。

仅用于本地类型生成：Monkeypatch 分析器为 MagicMock，避免触碰 PostgreSQL，
然后调用 _create_app() 并序列化 app.openapi()。产物为 gitignore 的本地文件。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import long_earn.app.app as app_module


def main() -> None:
    """导出 openapi.json 到 web/。"""
    # 仅构造应用路由，不触碰 PG（构造阶段即已避开真实分析器）。
    app_module.BacktestAnalyzer = MagicMock
    app_module.EventAnalyzer = MagicMock
    app = app_module._create_app()
    spec = app.openapi()
    out = Path(__file__).resolve().parents[1] / "web" / "openapi.json"
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"openapi written: {out}")


if __name__ == "__main__":
    main()
