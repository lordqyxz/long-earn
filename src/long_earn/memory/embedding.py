"""嵌入向量检索 — 支持 Ollama HTTP 后端的语义检索增强

为 MemoryStore 提供可选的嵌入模型混合检索能力，
在 TF-IDF 基础上引入语义相似度，提升中文/长文本/近义表达的匹配质量。

后端选择：
- ollama（默认）：通过 HTTP API 调用 ollama 的 embedding 模型（bge-m3），
  并可选使用 bge-reranker-v2-m3 对候选结果二次精排。
- sentence-transformers：本地加载模型（需安装 embed extra）。

当后端不可用时，MemoryStore.search 回退到纯 TF-IDF 检索。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

import numpy as np

from long_earn.memory.tfidf import cosine_similarity

logger = logging.getLogger(__name__)

# 可选：sentence-transformers 作为备用后端
_HAS_SENTENCE_TRANSFORMERS = False
try:
    from sentence_transformers import SentenceTransformer

    _HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    SentenceTransformer = None  # type: ignore[misc]

# 默认 ollama 端点
_DEFAULT_OLLAMA_URL = "http://localhost:11434"

# 重排时取 top-N 候选
_RERANK_CANDIDATES = 20


class EmbeddingRetriever:
    """嵌入向量检索器 — 提供语义级文本检索

    支持 ollama / sentence-transformers 两种后端。
    ollama 后端通过 HTTP 调用 bge-m3 生成嵌入，解决中文分词问题；
    可选 bge-reranker-v2-m3 对候选结果二次精排。

    当后端不可用时，退化至纯 TF-IDF 检索。
    """

    def __init__(  # noqa: PLR0913
        self,
        model_name: str = "bge-m3",
        base_url: str = _DEFAULT_OLLAMA_URL,
        backend: str = "ollama",
        device: str = "cpu",
        embedding_dim: int = 1024,
        timeout: int = 30,
        reranker_model: str = "bge-reranker-v2-m3",
        enable_reranker: bool = True,
    ):
        """初始化嵌入检索器

        Args:
            model_name: embedding 模型名称
            base_url: ollama 服务地址
            backend: 后端类型（ollama / sentence-transformers）
            device: 运行设备（仅 sentence-transformers 后端使用）
            embedding_dim: 嵌入向量维度（bge-m3 默认 1024）
            timeout: HTTP 请求超时秒数
            reranker_model: 重排模型名称
            enable_reranker: 是否启用重排（仅 ollama 后端有效）
        """
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.backend = backend
        self.device = device
        self.embedding_dim = embedding_dim
        self.timeout = timeout
        self.reranker_model = reranker_model
        self.enable_reranker = enable_reranker
        self._st_model: Any = None  # sentence-transformers 模型缓存
        self._available: bool | None = None  # ollama 可用性缓存
        self._cache: np.ndarray | None = None  # 事实嵌入矩阵缓存
        self._cache_version: int = -1  # 缓存版本（对应事实数量）

    # ── 可用性检测 ──────────────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        """检查嵌入后端是否可用。"""
        if self.backend == "ollama":
            return self._check_ollama()
        return _HAS_SENTENCE_TRANSFORMERS

    def _check_ollama(self) -> bool:
        """探测 ollama 服务是否可达且模型已拉取。"""
        if self._available is not None:
            return self._available
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/tags",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            models = {m.get("name", "") for m in data.get("models", [])}
            # 匹配模型名前缀（bge-m3 可能带 :latest 等 tag）
            available = any(m.startswith(self.model_name) for m in models)
            if not available:
                logger.warning(
                    f"ollama 服务在线但模型 {self.model_name} 未拉取，"
                    f"可用模型: {sorted(models)[:5]}..."
                )
            self._available = available
        except Exception as e:
            logger.info(f"ollama 嵌入服务不可用: {e}")
            self._available = False
        return self._available

    # ── 嵌入计算 ────────────────────────────────────────────────────────

    def _ollama_embed(self, texts: list[str]) -> np.ndarray | None:
        """调用 ollama /api/embed 批量获取嵌入向量。"""
        if not texts:
            return np.array([], dtype=np.float32)
        try:
            payload = json.dumps({
                "model": self.model_name,
                "input": texts,
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}/api/embed",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            embeddings = data.get("embeddings")
            if not embeddings:
                return None
            arr = np.array(embeddings, dtype=np.float32)
            # L2 归一化
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            return arr / norms
        except Exception as e:
            logger.error(f"ollama 嵌入计算失败: {e}")
            return None

    def _st_embed(self, texts: list[str]) -> np.ndarray | None:
        """sentence-transformers 后端批量嵌入。"""
        model = self._get_st_model()
        if model is None:
            return None
        try:
            return model.encode(
                texts, normalize_embeddings=True, show_progress_bar=False
            )
        except Exception as e:
            logger.error(f"sentence-transformers 嵌入失败: {e}")
            return None

    def _get_st_model(self) -> Any:
        """懒加载 sentence-transformers 模型。"""
        if self._st_model is not None:
            return self._st_model
        if not _HAS_SENTENCE_TRANSFORMERS or SentenceTransformer is None:
            return None
        try:
            self._st_model = SentenceTransformer(self.model_name, device=self.device)
            logger.info(
                f"sentence-transformers 模型已加载: {self.model_name} "
                f"(device={self.device})"
            )
            return self._st_model
        except Exception as e:
            logger.error(f"加载 sentence-transformers 模型失败: {e}")
            return None

    def _get_embeddings(self, texts: list[str]) -> np.ndarray | None:
        """批量获取文本嵌入（根据 backend 分发）。"""
        if not texts:
            return np.array([], dtype=np.float32)
        if self.backend == "ollama":
            return self._ollama_embed(texts)
        return self._st_embed(texts)

    # ── 重排 ────────────────────────────────────────────────────────────

    def _rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """使用 bge-reranker-v2-m3 对候选结果二次精排。

        Args:
            query: 查询文本
            candidates: embedding_search 返回的候选列表

        Returns:
            按重排分数降序排列的候选列表（含 _rerank_score 字段）
        """
        if not candidates or not self.enable_reranker:
            return candidates
        try:
            pairs = [
                {"query": query, "passage": c["content"]} for c in candidates
            ]
            payload = json.dumps({
                "model": self.reranker_model,
                "input": pairs,
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}/api/embed",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            scores = data.get("scores") or data.get("embeddings")
            if scores and len(scores) == len(candidates):
                for i, c in enumerate(candidates):
                    c["_rerank_score"] = float(scores[i])
                candidates.sort(key=lambda x: x["_rerank_score"], reverse=True)
        except Exception as e:
            logger.warning(f"重排失败（跳过，使用原顺序）: {e}")
        return candidates

    # ── 缓存 ────────────────────────────────────────────────────────────

    def _build_cache(self, store: Any) -> np.ndarray | None:
        """构建或更新缓存的事实嵌入矩阵。"""
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

    def invalidate_cache(self) -> None:
        """清除嵌入缓存。"""
        self._cache = None
        self._cache_version = -1

    # ── 纯嵌入检索 ──────────────────────────────────────────────────────

    def embedding_search(
        self,
        store: Any,
        query: str,
        k: int = 3,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        """纯嵌入向量检索（含可选重排）。

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

        query_emb = self._get_embeddings([query])
        if query_emb is None or query_emb.size == 0:
            return []

        doc_embeddings = self._build_cache(store)
        if doc_embeddings is None:
            return []

        sims = cosine_similarity(query_emb[0], doc_embeddings)

        scored = sorted(enumerate(sims), key=lambda x: x[1], reverse=True)

        results: list[dict[str, Any]] = []
        for idx, score in scored:
            if score < min_similarity:
                continue
            fact = store._facts[idx]
            results.append({
                "content": fact["content"],
                "metadata": dict(fact["metadata"]),
                "similarity": float(score),
            })
            if len(results) >= k:
                break

        # 重排
        if self.backend == "ollama" and self.enable_reranker:
            results = self._rerank(query, results)

        return results[:k]

    # ── 混合检索 ────────────────────────────────────────────────────────

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
        tfidf_results = store.search(
            query, k=store.fact_count or 10, **search_kwargs
        )

        if store.fact_count == 0:
            return []

        # 获取嵌入结果
        query_emb = self._get_embeddings([query])
        if query_emb is None or query_emb.size == 0:
            return tfidf_results[:k]

        doc_embeddings = self._build_cache(store)
        if doc_embeddings is None:
            return tfidf_results[:k]

        emb_sims = cosine_similarity(query_emb[0], doc_embeddings)

        # 融合评分
        indexed: dict[int, dict[str, Any]] = {}
        max_tfidf = max((r["similarity"] for r in tfidf_results), default=1.0)
        max_emb = float(np.max(emb_sims)) if emb_sims.size > 0 else 1.0

        # 按内容对齐 TF-IDF 结果到索引
        tfidf_by_idx: dict[int, float] = {}
        for r in tfidf_results:
            content = r["content"]
            for idx, fact in enumerate(store._facts):
                if fact["content"] == content:
                    tfidf_by_idx[idx] = r["similarity"] / max(max_tfidf, 1e-8)
                    break

        for idx in range(store.fact_count):
            tfidf_score = tfidf_by_idx.get(idx, 0.0)
            emb_score = float(emb_sims[idx]) / max(max_emb, 1e-8)
            fused = (1 - alpha) * tfidf_score + alpha * emb_score

            fact = store._facts[idx]
            meta = fact["metadata"]

            # 元数据过滤
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

        sorted_results = sorted(
            indexed.values(), key=lambda x: x["similarity"], reverse=True
        )
        candidates = sorted_results[:k]

        # 重排
        if self.backend == "ollama" and self.enable_reranker:
            candidates = self._rerank(query, candidates)

        return candidates[:k]
