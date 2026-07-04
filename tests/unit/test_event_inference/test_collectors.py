"""采集器测试 — Registry 行为 + Kimi/Ciccwm 采集器（mock 外部依赖）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from long_earn.event_inference.collectors.base import (
    CollectedItem,
    CollectorRegistry,
)
from long_earn.event_inference.collectors.ciccwm_collector import (
    CiccwmHotCollector,
    CiccwmTopicCollector,
)
from long_earn.event_inference.collectors.kimi_collector import KimiCollector


class _FakeCollector:
    """确定性采集器（测试用）。"""

    def __init__(self, name: str, items: list[CollectedItem], available: bool = True):
        self._name = name
        self._items = items
        self._available = available

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_available(self) -> bool:
        return self._available

    def collect(self, query: str, max_items: int = 10) -> list[CollectedItem]:
        return self._items[:max_items]


class _RaisingCollector:
    """采集时抛异常的采集器（验证异常隔离）。"""

    name = "raising"
    is_available = True

    def collect(self, query: str, max_items: int = 10) -> list[CollectedItem]:
        raise RuntimeError("boom")


# ── CollectorRegistry ──────────────────────────────────────────────────


class TestCollectorRegistry:
    def test_register_and_get(self):
        registry = CollectorRegistry()
        c = _FakeCollector("a", [])
        registry.register(c)
        assert registry.get("a") is c
        assert registry.count == 1

    def test_available_filters_unavailable(self):
        registry = CollectorRegistry()
        registry.register(_FakeCollector("up", [], available=True))
        registry.register(_FakeCollector("down", [], available=False))
        assert registry.available_count == 1
        assert registry.available()[0].name == "up"

    def test_collect_all_merges_results(self):
        registry = CollectorRegistry()
        registry.register(
            _FakeCollector(
                "a", [CollectedItem(title="t1", content="c1", source="a")]
            )
        )
        registry.register(
            _FakeCollector(
                "b", [CollectedItem(title="t2", content="c2", source="b")]
            )
        )
        items = registry.collect_all("茅台")
        assert len(items) == 2
        assert {it.source for it in items} == {"a", "b"}

    def test_collect_all_isolates_failures(self):
        """单个采集器异常不影响其他源。"""
        registry = CollectorRegistry()
        registry.register(
            _FakeCollector(
                "good", [CollectedItem(title="ok", content="ok", source="good")]
            )
        )
        registry.register(_RaisingCollector())
        items = registry.collect_all("query")
        assert len(items) == 1
        assert items[0].source == "good"

    def test_collect_all_empty_when_no_available(self):
        registry = CollectorRegistry()
        registry.register(_FakeCollector("down", [], available=False))
        assert registry.collect_all("q") == []


# ── KimiCollector ──────────────────────────────────────────────────────


class TestKimiCollector:
    def test_unavailable_without_api_key(self, monkeypatch):
        monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
        monkeypatch.delenv("KIMI_API_KEY", raising=False)
        c = KimiCollector()
        assert not c.is_available
        assert c.collect("q") == []

    def test_available_with_api_key(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test")
        c = KimiCollector()
        assert c.is_available

    def test_collect_parses_kimi_results(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test")
        c = KimiCollector()
        fake_results = [
            {"title": "茅台财报", "url": "http://a", "content": "净利润增长15%"},
            {"title": "噪音", "url": "", "content": ""},
        ]
        with patch(
            "long_earn.tools.kimi_web_search.kimi_web_search",
            return_value=fake_results,
        ):
            items = c.collect("茅台", max_items=5)
        assert len(items) == 1  # 空内容被过滤
        assert items[0].title == "茅台财报"
        assert items[0].source == "kimi"

    def test_collect_returns_empty_on_exception(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test")
        c = KimiCollector()
        with patch(
            "long_earn.tools.kimi_web_search.kimi_web_search",
            side_effect=RuntimeError("network"),
        ):
            assert c.collect("q") == []


# ── CiccwmCollector ────────────────────────────────────────────────────


def _make_mock_provider(available: bool = True, df: pd.DataFrame | None = None):
    provider = MagicMock()
    provider.is_available = available
    provider.get_hot_rank.return_value = df if df is not None else pd.DataFrame()
    provider.get_topic_news.return_value = df if df is not None else pd.DataFrame()
    return provider


class TestCiccwmHotCollector:
    def test_unavailable_when_provider_unavailable(self):
        provider = _make_mock_provider(available=False)
        c = CiccwmHotCollector(provider)
        assert not c.is_available
        assert c.collect("q") == []

    def test_collect_converts_dataframe(self):
        df = pd.DataFrame(
            [
                {"title": "央行降准", "content": "释放长期资金", "redirect_url": "http://a"},
                {"title": "", "content": ""},
            ]
        )
        provider = _make_mock_provider(available=True, df=df)
        c = CiccwmHotCollector(provider)
        items = c.collect("q")
        assert len(items) == 1  # 标题+内容均空 → 过滤
        assert items[0].title == "央行降准"
        assert items[0].source == "ciccwm_hot"

    def test_collect_empty_dataframe(self):
        provider = _make_mock_provider(available=True, df=pd.DataFrame())
        c = CiccwmHotCollector(provider)
        assert c.collect("q") == []

    def test_collect_isolates_exceptions(self):
        provider = MagicMock()
        provider.is_available = True
        provider.get_hot_rank.side_effect = RuntimeError("http error")
        c = CiccwmHotCollector(provider)
        assert c.collect("q") == []


class TestCiccwmTopicCollector:
    def test_collect_converts_dataframe(self):
        df = pd.DataFrame(
            [{"title": "新能源补贴", "summary": "政策延续", "url": "http://b"}]
        )
        provider = _make_mock_provider(available=True, df=df)
        c = CiccwmTopicCollector(provider)
        items = c.collect("q")
        assert len(items) == 1
        assert items[0].content == "政策延续"  # summary 字段被识别
        assert items[0].source == "ciccwm_topic"
