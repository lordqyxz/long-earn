"""tests/unit 专用 conftest：禁止 PG 集成测试在 unit 套件中 skip 假绿。"""

from __future__ import annotations

import pytest

from long_earn.core.pg import pg_version


def _pg_available() -> bool:
    try:
        pg_version()
        return True
    except Exception:
        return False


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """unit 目录内标记 integration 的用例在 PG 不可达时 fail，而非 skip。"""
    if item.get_closest_marker("integration") and not _pg_available():
        pytest.fail(
            "集成测试需要 PostgreSQL，但服务不可用（unit 目录禁止 skip 假绿）",
            pytrace=False,
        )
