"""嵌入向量检索 — 基于 Sentence-Transformers 的语义检索增强

为 MemoryStore 提供可选的嵌入模型混合检索能力，
在 TF-IDF 基础上引入语义相似度，提升长文本和近义表达的匹配质量。

使用方式：
    retriever = EmbeddingRetriever()
    store = MemoryStore()
    results = retriever.hybrid_search(store, "动量策略", k=3, alpha=0.5)

如果 sentence-transformers 未安装，回退到纯 TF-IDF 检索。
"""

from typing import Any

import numpy as np
from loguru import logger

from long_earn.memory.tfidf import cosine_similarity

_HAS_SENTENCE_TRANSFORMERS = False
try:
    from sentence_transformers import SentenceTransformer

    _HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    SentenceTransformer = None  # type: ignore[misc]


class EmbeddingRetriever:
    """嵌入向量检索器 — 提供语义级文本检索

    使用 sentence-transformers 的轻量级模型（默认 all-MiniLM-L6-v2）生成文本嵌入，
    在 TF-IDF 检索基础上叠加语义相似度评分。

    当 sentence-transformers 不可用时，退化至纯 TF-IDF 检索。
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        device: str = "cpu",
        embedding_dim: int = 384,
    ):
        """初始化嵌入检索器

        Args:
            model_name: Sentence-Transformer 模型名称
            device: 运行设备（cpu / cuda）
            embedding_dim: 嵌入向量维度（用于缓存分配）
        """
        self.model_name = model_name
        self.device = device
        self.embedding_dim = embedding_dim
        self._model: Any = None
        self._cache: np.ndarray | None = None  # 缓存的事实嵌入矩阵
        self._cache_version: int = -1  # 缓存版本（对应事实数量）

    @property
    def is_available(self) -> bool:
        """检查嵌入模型是否可用"""
        return _HAS_SENTENCE_TRANSFORMERS

    def _get_model(self) -> Any:
        """懒加载嵌入模型"""
        if self._model is not None:
            return self._model
        if not _HAS_SENTENCE_TRANSFORMERS or SentenceTransformer is None:
            logger.warning(
                "sentence-transformers 未安装，嵌入检索不可用。"
                "安装: uv run pip install sentence-transformers"
            )
            return None
        try:
            self._model = SentenceTransformer(self.model_name, device=self.device)
            logger.info(f"嵌入模型已加载: {self.model_name} (device={self.device})")
            return self._model
        except Exception as e:
            logger.error(f"加载嵌入模型失败: {e}")
            self._model = None
            return None

    def _get_embeddings(self, texts: list[str]) -> np.ndarray | None:
        """批量获取文本嵌入

        Args:
            texts: 文本列表

        Returns:
            嵌入矩阵 (n_texts, embedding_dim)，失败返回 None
        """
        model = self._get_model()
        if model is None:
            return None
        try:
            return model.encode(
                texts, normalize_embeddings=True, show_progress_bar=False
            )
        except Exception as e:
            logger.error(f"嵌入计算失败: {e}")
            return None

    def _build_cache(self, store: Any) -> np.ndarray | None:
        """构建或更新缓存的事实嵌入矩阵

        Args:
            store: MemoryStore 实例

        Returns:
            缓存矩阵
        """
        fact_count = store.fact_count
        if self._cache is not None and self._cache_version == fact_count:
            return self._cache

        if fact_count == 0:
            self._cache = None
            self._cache_version = 0
            return None

        texts = [f["content"] for f in store._facts]
        embeddings = self._get_embeddings(texts)
        if embeddings is None:
            return None

        self._cache = embeddings
        self._cache_version = fact_count
        return self._cache

    def embedding_search(
        self,
        store: Any,
        query: str,
        k: int = 3,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        """纯嵌入向量检索

        Args:
            store: MemoryStore 实例
            query: 查询文本
            k: 返回结果数量
            min_similarity: 最小相似度阈值

        Returns:
            与 MemoryStore.search() 相同格式的检索结果
        """
        if store.fact_count == 0:
            return []

        # 获取查询嵌入
        model = self._get_model()
        if model is None:
            return []

        query_emb = model.encode(
            [query], normalize_embeddings=True, show_progress_bar=False
        )[0]

        # 获取或缓存事实嵌入
        doc_embeddings = self._build_cache(store)
        if doc_embeddings is None:
            return []

        # 计算余弦相似度
        sims = cosine_similarity(query_emb, doc_embeddings)

        # 排序并过滤
        scored = sorted(enumerate(sims), key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in scored:
            if score < min_similarity:
                continue
            fact = store._facts[idx]
            results.append(
                {
                    "content": fact["content"],
                    "metadata": dict(fact["metadata"]),
                    "similarity": float(score),
                }
            )
            if len(results) >= k:
                break

        return results

    def hybrid_search(
        self,
        store: Any,
        query: str,
        k: int = 3,
        alpha: float = 0.5,
        **search_kwargs,
    ) -> list[dict[str, Any]]:
        """混合检索：TF-IDF + 嵌入向量融合评分

        将 TF-IDF 余弦相似度与嵌入语义相似度加权融合。
        alpha=0.0 时完全使用 TF-IDF，alpha=1.0 时完全使用嵌入。

        Args:
            store: MemoryStore 实例
            query: 查询文本
            k: 返回结果数量
            alpha: 混合权重（0~1），越高越偏重语义
            **search_kwargs: 传递给 store.search() 的额外参数

        Returns:
            与 MemoryStore.search() 相同格式的结果
        """
        # 获取 TF-IDF 结果
        tfidf_results = store.search(query, k=store.fact_count or 10, **search_kwargs)

        if not _HAS_SENTENCE_TRANSFORMERS or store.fact_count == 0:
            return tfidf_results[:k]

        # 获取嵌入结果
        model = self._get_model()
        if model is None:
            return tfidf_results[:k]

        query_emb = model.encode(
            [query], normalize_embeddings=True, show_progress_bar=False
        )[0]
        doc_embeddings = self._build_cache(store)
        if doc_embeddings is None:
            return tfidf_results[:k]

        emb_sims = cosine_similarity(query_emb, doc_embeddings)

        # 融合评分：将 TF-IDF 分数和嵌入分数加权合并
        indexed: dict[int, dict[str, Any]] = {}
        max_tfidf = max((r["similarity"] for r in tfidf_results), default=1.0)
        max_emb = float(np.max(emb_sims)) if emb_sims.size > 0 else 1.0

        # 处理 TF-IDF 结果（按索引对齐）
        tfidf_by_idx: dict[int, float] = {}
        for r in tfidf_results:
            # 需要找到对应的事实索引
            content = r["content"]
            for idx, fact in enumerate(store._facts):
                if fact["content"] == content:
                    tfidf_by_idx[idx] = r["similarity"] / max(max_tfidf, 1e-8)
                    break

        # 融合评分
        for idx in range(store.fact_count):
            tfidf_score = tfidf_by_idx.get(idx, 0.0)
            emb_score = float(emb_sims[idx]) / max(max_emb, 1e-8)

            # 加权融合
            fused = (1 - alpha) * tfidf_score + alpha * emb_score

            fact = store._facts[idx]
            meta = fact["metadata"]

            # 应用 search_kwargs 中的元数据过滤
            categories = search_kwargs.get("categories")
            terms = search_kwargs.get("terms")
            source_files = search_kwargs.get("source_files")
            include_decayed = search_kwargs.get("include_decayed", False)

            if not include_decayed and meta.get("decayed", False):
                continue
            if categories and not any(
                cat in meta.get("category", "") for cat in categories
            ):
                continue
            if terms and not any(t in meta.get("term", "") for t in terms):
                continue
            if source_files and meta.get("source_file", "") not in source_files:
                continue

            indexed[idx] = {
                "content": fact["content"],
                "metadata": dict(meta),
                "similarity": round(fused, 4),
                "_tfidf_score": round(tfidf_score, 4),
                "_embedding_score": round(emb_score, 4),
            }

        # 排序
        sorted_results = sorted(
            indexed.values(), key=lambda x: x["similarity"], reverse=True
        )
        return sorted_results[:k]

    def invalidate_cache(self) -> None:
        """清除嵌入缓存

        在添加大量事实后调用以确保检索正确性。
        """
        self._cache = None
        self._cache_version = -1
