"""事件流分析器 — 消费 SubstanceStore 中的 EVENT/RELATION 物质（ADR-007 Phase 3）。

与 ``BacktestAnalyzer``（消费 DuckDB 审计日志）并列，本模块消费物质-运动架构
中的事件物质，为 Dashboard 提供 REST API 查询能力。

设计原则：
- 只读 SubstanceStore，不修改物质状态
- 返回纯 dict/list，便于 JSON 序列化
- 与 ``MemoryServiceImpl.activate_events`` 共享物质元数据契约
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from long_earn.substance.model import Substance, SubstanceForm
from long_earn.substance.store import SubstanceStore


class EventAnalyzer:
    """事件流分析器 — 查询 SubstanceStore 中的事件/关系物质。

    Usage::

        analyzer = EventAnalyzer()
        analyzer.load("~/.long_earn/substances.jsonl")
        events = analyzer.list_events(limit=50, symbol="600519")
    """

    def __init__(self, store: SubstanceStore | None = None) -> None:
        self._store = store or SubstanceStore()
        self._loaded = False

    # ── 加载 ──────────────────────────────────────────────────

    def load(self, path: str | Path) -> bool:
        """从 JSONL 文件加载物质。"""
        path = Path(path).expanduser()
        if not path.exists():
            logger.warning(f"物质文件不存在: {path}")
            return False
        ok = self._store.load(path)
        self._loaded = ok
        return ok

    def attach(self, store: SubstanceStore) -> None:
        """直接附加一个已加载的 SubstanceStore（共享引用，零拷贝）。"""
        self._store = store
        self._loaded = store.count > 0

    @property
    def is_ready(self) -> bool:
        return self._loaded and self._store.count > 0

    @property
    def store(self) -> SubstanceStore:
        return self._store

    # ── 查询 ──────────────────────────────────────────────────

    def list_events(
        self,
        limit: int = 50,
        symbol: str | None = None,
        sentiment: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出事件物质，按创建时间倒序。

        Args:
            limit: 返回条数上限
            symbol: 按标的代码过滤（模糊匹配 symbols 列表）
            sentiment: 按情绪过滤（positive/negative/neutral）
            category: 按事件类别过滤
        """
        if not self.is_ready:
            return []

        events = [
            s for s in self._store.get_all() if s.form is SubstanceForm.EVENT
        ]
        results: list[dict[str, Any]] = []
        for s in events:
            meta = s.metadata
            symbols = meta.get("symbols", []) or []

            if symbol and not any(symbol in sym for sym in symbols):
                continue
            if sentiment and meta.get("sentiment", "neutral") != sentiment:
                continue
            if category:
                ev_cat = meta.get("event_category", "")
                if not category or category not in ev_cat:
                    continue

            results.append(self._format_event(s))

        results.sort(key=lambda r: r["created_at"], reverse=True)
        return results[:limit]

    def list_relations(
        self,
        limit: int = 50,
        target: str | None = None,
        direction: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出影响关系物质，按置信度倒序。"""
        if not self.is_ready:
            return []

        relations = [
            s for s in self._store.get_all() if s.form is SubstanceForm.RELATION
        ]
        results: list[dict[str, Any]] = []
        for s in relations:
            meta = s.metadata
            if target:
                rel_target = meta.get("target", s.target_id or "")
                if not rel_target or target not in rel_target:
                    continue
            if direction and meta.get("direction", "neutral") != direction:
                continue

            results.append(self._format_relation(s))

        results.sort(key=lambda r: r["confidence"], reverse=True)
        return results[:limit]

    def event_stats(self) -> dict[str, Any]:
        """事件统计 — 情绪分布、类别分布、热门标的、总数。"""
        if not self.is_ready:
            return {
                "total_events": 0,
                "total_relations": 0,
                "by_sentiment": {},
                "by_category": {},
                "top_symbols": [],
            }

        events = [
            s for s in self._store.get_all() if s.form is SubstanceForm.EVENT
        ]
        relations = [
            s for s in self._store.get_all() if s.form is SubstanceForm.RELATION
        ]

        sentiment_counter: Counter[str] = Counter()
        category_counter: Counter[str] = Counter()
        symbol_counter: Counter[str] = Counter()

        for s in events:
            meta = s.metadata
            sentiment_counter[meta.get("sentiment", "neutral")] += 1
            cat = meta.get("event_category", "")
            if cat:
                category_counter[cat] += 1
            for sym in meta.get("symbols", []) or []:
                symbol_counter[sym] += 1

        return {
            "total_events": len(events),
            "total_relations": len(relations),
            "by_sentiment": dict(sentiment_counter),
            "by_category": dict(category_counter.most_common(10)),
            "top_symbols": [
                {"symbol": sym, "count": cnt}
                for sym, cnt in symbol_counter.most_common(10)
            ],
        }

    def event_timeline(self, days: int = 30) -> list[dict[str, Any]]:
        """事件时间线 — 按天聚合事件数。

        Args:
            days: 最近 N 天
        """
        if not self.is_ready:
            return []

        events = [
            s for s in self._store.get_all() if s.form is SubstanceForm.EVENT
        ]
        today = datetime.now().date()
        daily: dict[str, dict[str, Any]] = {}
        for s in events:
            # 按自然日比较，避免时间精度导致 days 边界过滤错误
            age_days = (today - s.created_at.date()).days
            if age_days > days:
                continue
            day_key = s.created_at.strftime("%Y-%m-%d")
            bucket = daily.setdefault(
                day_key,
                {"date": day_key, "count": 0, "positive": 0, "negative": 0, "neutral": 0},
            )
            bucket["count"] += 1
            sentiment = s.metadata.get("sentiment", "neutral")
            if sentiment in bucket:
                bucket[sentiment] += 1

        return sorted(daily.values(), key=lambda r: r["date"])

    def get_event(self, sid: str) -> dict[str, Any] | None:
        """按 sid 获取单个事件详情 + 关联关系。"""
        if not self.is_ready:
            return None
        s = self._store.get_by_sid(sid)
        if s is None or s.form is not SubstanceForm.EVENT:
            return None

        event = self._format_event(s)
        # 关联关系：source_id 指向该事件的关系
        relations: list[dict[str, Any]] = []
        for r in self._store.get_all():
            if r.form is SubstanceForm.RELATION and r.source_id == sid:
                relations.append(self._format_relation(r))
        event["relations"] = relations
        return event

    # ── 内部格式化 ──────────────────────────────────────────────

    @staticmethod
    def _format_event(s: Substance) -> dict[str, Any]:
        meta = s.metadata
        return {
            "sid": s.sid,
            "content": s.content,
            "sentiment": meta.get("sentiment", "neutral"),
            "symbols": meta.get("symbols", []) or [],
            "category": meta.get("event_category", ""),
            "confidence": s.confidence,
            "created_at": s.created_at.isoformat(),
            "conflict_group": s.conflict_group,
            "keys": s.keys,
            "source": s.source,
        }

    @staticmethod
    def _format_relation(s: Substance) -> dict[str, Any]:
        meta = s.metadata
        return {
            "sid": s.sid,
            "content": s.content,
            "source_id": s.source_id,
            "target": meta.get("target", s.target_id or ""),
            "relation_type": s.relation_type or "impacts",
            "direction": meta.get("direction", "neutral"),
            "confidence": s.confidence,
            "created_at": s.created_at.isoformat(),
        }
