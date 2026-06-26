"""RetrievalIndex — 粒子态检索，关键词通道 + 语义通道双通道融合。

关键词通道：dict 倒排索引（key → sid 列表），正则 key 编译存储。
语义通道：TF-IDF（IDF 升序保留区分性强的罕见词 + jieba 分词）。
融合：keyword 命中优先，semantic 补充，alpha 加权。
增量更新：新物质只 transform 不 refit；定期全量 refit。
"""

from __future__ import annotations

import contextlib
import math
import re
from collections import defaultdict
from typing import Any

import numpy as np

try:
    import jieba

    _HAS_JIEBA = True
except ImportError:
    jieba = None  # type: ignore[misc]
    _HAS_JIEBA = False

from long_earn.substance.model import FilterLogic, Substance


def _tokenize(text: str) -> list[str]:
    """分词 — jieba 优先，回退到正则中英文切分。"""
    if _HAS_JIEBA and jieba is not None:
        return [t.strip() for t in jieba.cut(text) if t.strip()]
    return re.findall(r"[一-鿿]+|[a-zA-Z0-9_]{2,}", text.lower())


class _TfidfChannel:
    """语义通道 — TF-IDF 向量化，修复 IDF 特征选择。"""

    def __init__(self, max_features: int = 5000) -> None:
        self.max_features = max_features
        self._vocabulary: dict[str, int] = {}
        self._idf: np.ndarray | None = None
        self._doc_matrix: np.ndarray | None = None
        self._sid_order: list[str] = []
        self._dirty = True

    def fit(self, substances: list[Substance]) -> None:
        """全量拟合词汇表和文档矩阵。"""
        docs = [s.content for s in substances]
        if not docs:
            self._vocabulary = {}
            self._idf = None
            self._doc_matrix = np.array([])
            self._sid_order = [s.sid for s in substances]
            self._dirty = False
            return

        doc_count = len(docs)
        df: dict[str, float] = defaultdict(float)
        for doc in docs:
            seen = set(_tokenize(doc))
            for token in seen:
                df[token] += 1

        # IDF 升序：保留区分性强的罕见词（修复旧系统降序的反转缺陷）
        sorted_terms = sorted(df.items(), key=lambda x: x[1])
        sorted_terms = sorted_terms[: self.max_features]
        self._vocabulary = {term: idx for idx, (term, _) in enumerate(sorted_terms)}
        n_terms = len(self._vocabulary)

        self._idf = np.ones(n_terms)
        for term, idx in self._vocabulary.items():
            if self._idf is not None:
                self._idf[idx] = math.log((doc_count + 1) / (df[term] + 1)) + 1.0

        self._doc_matrix = self._transform_batch(docs)
        self._sid_order = [s.sid for s in substances]
        self._dirty = False

    def _transform_batch(self, docs: list[str]) -> np.ndarray:
        """将文档列表转换为 TF-IDF 矩阵。"""
        if self._idf is None:
            return np.array([])
        n_terms = len(self._vocabulary)
        matrix = np.zeros((len(docs), n_terms), dtype=np.float32)
        for i, doc in enumerate(docs):
            tokens = _tokenize(doc)
            tf = np.zeros(n_terms)
            for token in tokens:
                idx = self._vocabulary.get(token)
                if idx is not None:
                    tf[idx] += 1
            norm = np.linalg.norm(tf)
            if norm > 0:
                tf /= norm
            matrix[i] = tf * self._idf
        return matrix

    def query(self, text: str, k: int) -> list[tuple[str, float]]:
        """查询语义通道，返回 [(sid, score), ...]。"""
        if self._doc_matrix is None or self._doc_matrix.size == 0:
            return []
        query_vec = self._transform_batch([text])[0]
        if query_vec is None:
            return []
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return []
        query_vec = query_vec / query_norm

        doc_norms = np.linalg.norm(self._doc_matrix, axis=1)
        doc_norms[doc_norms == 0] = 1.0
        normalized = self._doc_matrix / doc_norms[:, np.newaxis]
        sims = normalized @ query_vec

        scored = sorted(
            zip(self._sid_order, sims, strict=True),
            key=lambda x: x[1],
            reverse=True,
        )
        return [(sid, float(score)) for sid, score in scored[:k] if score > 0]

    @property
    def is_fitted(self) -> bool:
        return self._idf is not None and not self._dirty

    def mark_dirty(self) -> None:
        self._dirty = True


class RetrievalIndex:
    """双通道检索索引 — 关键词 + 语义融合。"""

    def __init__(self, max_features: int = 5000, alpha: float = 0.5) -> None:
        self.alpha = alpha
        self._keyword_index: dict[str, list[str]] = defaultdict(list)
        self._regex_keys: dict[str, re.Pattern[str]] = {}
        self._semantic = _TfidfChannel(max_features=max_features)
        self._substances_by_sid: dict[str, Substance] = {}

    def rebuild(self, substances: list[Substance]) -> None:
        """全量重建索引。"""
        self._keyword_index.clear()
        self._regex_keys.clear()
        self._substances_by_sid = {s.sid: s for s in substances}

        for s in substances:
            for key in s.keys:
                self._keyword_index[key].append(s.sid)
                with contextlib.suppress(re.error):
                    self._regex_keys[key] = re.compile(key)

        self._semantic.fit(substances)

    def _keyword_match(self, text: str) -> dict[str, float]:
        """关键词通道：扫描文本命中哪些 key，返回 sid → score。"""
        scores: dict[str, float] = defaultdict(float)
        for key, sids in self._keyword_index.items():
            pattern = self._regex_keys.get(key)
            if pattern is not None:
                if pattern.search(text):
                    for sid in sids:
                        scores[sid] += 1.0
            elif key in text:
                for sid in sids:
                    scores[sid] += 1.0
        return dict(scores)

    def _apply_filter_logic(self, s: Substance, text: str) -> bool:
        """应用 WorldInfo filter_keys 过滤逻辑。"""
        if not s.filter_keys:
            return True
        matches = [fk in text for fk in s.filter_keys]
        match s.filter_logic:
            case FilterLogic.AND_ANY:
                return any(matches)
            case FilterLogic.AND_ALL:
                return all(matches)
            case FilterLogic.NOT_ANY:
                return not any(matches)
            case FilterLogic.NOT_ALL:
                return not all(matches)
        return True  # 不可达兜底

    def search(
        self,
        query: str,
        k: int = 10,
        visible_at: Any | None = None,
        alpha: float | None = None,
    ) -> list[dict[str, Any]]:
        """双通道融合检索。

        Args:
            query: 查询文本
            k: 返回结果数
            visible_at: 时间过滤时刻（防未来函数）
            alpha: 融合权重，None 用实例默认

        Returns:
            [{sid, content, similarity, metadata, source}, ...]
        """
        blend = alpha if alpha is not None else self.alpha

        kw_scores = self._keyword_match(query)
        sem_results = self._semantic.query(query, k=k * 3)
        sem_scores = dict(sem_results)

        all_sids = set(kw_scores) | set(sem_scores)
        fused: list[dict[str, Any]] = []

        for sid in all_sids:
            s = self._substances_by_sid.get(sid)
            if s is None:
                continue
            if visible_at is not None and not s.is_visible_at(visible_at):
                continue
            if not self._apply_filter_logic(s, query):
                continue

            kw = kw_scores.get(sid, 0.0)
            sem = sem_scores.get(sid, 0.0)
            # 归一化后加权融合
            max_kw = max(kw_scores.values()) if kw_scores else 1.0
            max_sem = max(sem_scores.values()) if sem_scores else 1.0
            kw_norm = kw / max_kw if max_kw > 0 else 0.0
            sem_norm = sem / max_sem if max_sem > 0 else 0.0
            score = (1 - blend) * kw_norm + blend * sem_norm

            fused.append(
                {
                    "sid": sid,
                    "content": s.content,
                    "similarity": round(score, 4),
                    "metadata": s.metadata,
                    "source": s.source,
                    "form": s.form.value,
                }
            )

        fused.sort(key=lambda x: x["similarity"], reverse=True)
        return fused[:k]

    @property
    def substance_count(self) -> int:
        return len(self._substances_by_sid)
