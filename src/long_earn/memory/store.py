"""记忆存储 — 综合事实 + 向量检索 + 关系图的统一记忆系统

基于 numpy/pandas 技术底座，无外部向量数据库依赖。
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from long_earn.memory.graph import RelationGraph
from long_earn.memory.tfidf import TfidfVectorizer, cosine_similarity

logger = logging.getLogger(__name__)


class MemoryStore:
    """综合事实存储 — 提供知识检索、事实管理、关系图三大能力

    设计原则：
    - 事实 (facts): pandas DataFrame，存储文本内容和元数据
    - 向量 (vectors): numpy 矩阵，TF-IDF 向量化的文档表示
    - 关系 (relations): numpy 邻接矩阵，实体间的关系图

    Usage:
        store = MemoryStore()
        store.add_fact("夏普比率衡量...", metadata={"term": "夏普比率", "category": "风险指标"})
        results = store.search("风险调整收益", k=3)
        store.add_relation("夏普比率", "sortino_ratio", weight=0.8)
    """

    def __init__(self, max_features: int = 5000):
        self.max_features = max_features
        self._facts: list[dict[str, Any]] = []
        self._fact_texts: list[str] = []
        self._vectorizer = TfidfVectorizer(max_features=max_features)
        self._doc_matrix: np.ndarray | None = None
        self._dirty = True  # 标记向量矩阵是否需要重建
        self.graph = RelationGraph()

    # ── 事实管理 ──────────────────────────────────────────────

    def add_fact(self, content: str, metadata: dict[str, Any] | None = None) -> int:
        """添加一条事实/知识

        Args:
            content: 文本内容
            metadata: 元数据 (term, category, source_file 等)

        Returns:
            事实索引
        """
        meta = metadata or {}
        meta.setdefault("created_at", datetime.now().isoformat())
        meta.setdefault("fact_id", f"fact_{len(self._facts):06d}")

        self._facts.append({"content": content, "metadata": meta})
        self._fact_texts.append(content)
        self._dirty = True
        return len(self._facts) - 1

    def add_facts(self, items: list[tuple[str, dict[str, Any]]]) -> list[int]:
        """批量添加事实"""
        indices = []
        for content, metadata in items:
            indices.append(self.add_fact(content, metadata))
        return indices

    def get_fact(self, index: int) -> dict[str, Any] | None:
        """获取指定索引的事实"""
        if 0 <= index < len(self._facts):
            return self._facts[index]
        return None

    def get_fact_by_id(self, fact_id: str) -> dict[str, Any] | None:
        """按 fact_id 获取事实"""
        for fact in self._facts:
            if fact["metadata"].get("fact_id") == fact_id:
                return fact
        return None

    def get_all_facts(self) -> pd.DataFrame:
        """获取所有事实的 DataFrame"""
        rows = []
        for i, fact in enumerate(self._facts):
            row = {"index": i, "content": fact["content"]}
            row.update(fact["metadata"])
            rows.append(row)
        return pd.DataFrame(rows)

    @property
    def fact_count(self) -> int:
        return len(self._facts)

    # ── 向量化与检索 ──────────────────────────────────────────

    def _ensure_vectors(self) -> None:
        """确保向量矩阵是最新的"""
        if not self._dirty and self._doc_matrix is not None:
            return
        if not self._fact_texts:
            self._doc_matrix = np.array([])
            self._dirty = False
            return

        self._doc_matrix = self._vectorizer.fit_transform(self._fact_texts)
        self._dirty = False

    def search(  # noqa: PLR0913
        self,
        query: str,
        k: int = 3,
        categories: list[str] | None = None,
        terms: list[str] | None = None,
        source_files: list[str] | None = None,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        """搜索记忆库

        Args:
            query: 搜索查询
            k: 返回结果数量
            categories: 按类别过滤
            terms: 按词条名称过滤
            source_files: 按源文件过滤
            min_similarity: 最小相似度阈值

        Returns:
            搜索结果列表，每项包含 content, metadata, similarity
        """
        self._ensure_vectors()

        if self._doc_matrix is None or self._doc_matrix.size == 0:
            return []

        # 向量化查询
        query_vec = self._vectorizer.transform([query])[0]

        # 计算余弦相似度
        similarities = cosine_similarity(query_vec, self._doc_matrix)

        # 排序并过滤
        scored = sorted(
            enumerate(similarities),
            key=lambda x: x[1],
            reverse=True,
        )

        results = []
        for idx, score in scored:
            if score < min_similarity:
                continue

            fact = self._facts[idx]
            meta = fact["metadata"]

            # 元数据过滤
            if categories:
                category = meta.get("category", "")
                if not any(cat in category for cat in categories):
                    continue

            if terms:
                term = meta.get("term", "")
                if not any(t in term for t in terms):
                    continue

            if source_files:
                source = meta.get("source_file", "")
                if source not in source_files:
                    continue

            results.append(
                {
                    "content": fact["content"],
                    "metadata": meta,
                    "similarity": float(score),
                }
            )

            if len(results) >= k:
                break

        return results

    def search_as_strings(
        self,
        query: str,
        k: int = 3,
        **kwargs,
    ) -> list[str]:
        """搜索并返回格式化字符串"""
        results = self.search(query, k=k, **kwargs)
        output = []
        for r in results:
            meta = r["metadata"]
            source = meta.get("source_file", "unknown")
            term_name = meta.get("term", "")
            category = meta.get("category", "")
            content = r["content"][:500]

            header = f"【来源: {source}"
            if term_name:
                header += f" | 词条: {term_name}"
            if category:
                header += f" | 类别: {category}"
            header += "】"

            output.append(f"{header}\n{content}\n")
        return output

    # ── 关系图 ────────────────────────────────────────────────

    def add_relation(self, source: str, target: str, weight: float = 1.0) -> None:
        """添加实体间的关系

        Args:
            source: 源实体 ID
            target: 目标实体 ID
            weight: 关系强度 (0-1)
        """
        self.graph.add_edge(source, target, weight)

    def get_related(self, entity_id: str, depth: int = 2) -> list[str]:
        """获取关联实体"""
        return self.graph.get_related(entity_id, depth=depth)

    # ── 持久化 ────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        """保存记忆到磁盘（使用 numpy .npz 格式）"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        self._ensure_vectors()

        facts_df = self.get_all_facts()
        relations_df = self.graph.to_dataframe()

        np.savez_compressed(
            path,
            doc_matrix=(
                self._doc_matrix if self._doc_matrix is not None else np.array([])
            ),
            allow_pickle=True,
        )
        facts_df.to_pickle(path.with_suffix(".facts.pkl"))
        if not relations_df.empty:
            relations_df.to_pickle(path.with_suffix(".relations.pkl"))

        logger.info(f"记忆已保存: {path} ({self.fact_count} 条事实)")

    def load(self, path: str | Path) -> bool:
        """从磁盘加载记忆"""
        path = Path(path)
        facts_path = path.with_suffix(".facts.pkl")
        vecs_path = path

        if not facts_path.exists():
            logger.warning(f"记忆文件不存在: {facts_path}")
            return False

        try:
            facts_df = pd.read_pickle(facts_path)
            self._facts = []
            self._fact_texts = []
            for _, row in facts_df.iterrows():
                meta = {k: v for k, v in row.items() if k not in ("index", "content")}
                content = row["content"]
                self._facts.append({"content": content, "metadata": meta})
                self._fact_texts.append(content)

            if vecs_path.exists():
                data = np.load(vecs_path, allow_pickle=True)
                if "doc_matrix" in data:
                    self._doc_matrix = data["doc_matrix"]
                    self._dirty = False

            relations_path = path.with_suffix(".relations.pkl")
            if relations_path.exists():
                relations_df = pd.read_pickle(relations_path)
                for _, row in relations_df.iterrows():
                    self.graph.add_edge(row["source"], row["target"], row["weight"])

            logger.info(f"记忆已加载: {path} ({self.fact_count} 条事实)")
            return True
        except Exception as e:
            logger.error(f"加载记忆失败: {e}")
            return False

    # ── 文档加载（Markdown 标题感知切分）───────────────────────

    def load_markdown(
        self,
        file_path: str | Path,
        chunk_size: int = 1500,
        chunk_overlap: int = 200,
    ) -> int:
        """加载 Markdown 文件并按标题切分存入记忆

        Args:
            file_path: 文件路径
            chunk_size: 切片大小
            chunk_overlap: 切片重叠大小

        Returns:
            加载的切片数量
        """
        file_path = Path(file_path)
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"读取文件失败 {file_path}: {e}")
            return 0

        chunks = self._split_markdown(
            content, file_path.name, chunk_size, chunk_overlap
        )
        for chunk_text, meta in chunks:
            self.add_fact(chunk_text, metadata=meta)

        logger.info(f"已加载 {file_path.name}: {len(chunks)} 个切片")
        return len(chunks)

    def load_text(
        self,
        file_path: str | Path,
        chunk_size: int = 1500,
        chunk_overlap: int = 200,
    ) -> int:
        """加载纯文本文件并切分"""
        file_path = Path(file_path)
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"读取文件失败 {file_path}: {e}")
            return 0

        count = 0
        start = 0
        while start < len(content):
            end = min(start + chunk_size, len(content))
            chunk_text = content[start:end]
            if chunk_text.strip():
                self.add_fact(
                    chunk_text,
                    metadata={
                        "source_file": file_path.name,
                        "chunk_start": start,
                        "chunk_end": end,
                    },
                )
                count += 1
            start += chunk_size - chunk_overlap

        logger.info(f"已加载 {file_path.name}: {count} 个切片")
        return count

    def load_directory(
        self,
        directory: str | Path,
        extensions: set[str] | None = None,
    ) -> int:
        """加载目录中的所有支持文件"""
        directory = Path(directory)
        if not directory.exists():
            logger.warning(f"目录不存在: {directory}")
            return 0

        extensions = extensions or {".md", ".txt", ".py"}
        total = 0

        for file_path in sorted(directory.iterdir()):
            if not file_path.is_file():
                continue
            suffix = file_path.suffix.lower()
            if suffix not in extensions:
                continue

            if suffix == ".md":
                total += self.load_markdown(file_path)
            elif suffix in (".txt", ".py"):
                total += self.load_text(file_path)

        logger.info(f"目录加载完成: {directory} ({total} 条事实)")
        return total

    @staticmethod
    def _chunk_long_text(
        text: str,
        chunk_size: int,
        chunk_overlap: int,
        base_meta: dict[str, Any],
    ) -> list[tuple[str, dict[str, Any]]]:
        """将长文本切分为重叠的固定大小片段"""
        chunks: list[tuple[str, dict[str, Any]]] = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunk_text = text[start:end].strip()
            if chunk_text:
                meta = dict(base_meta)
                meta["chunk_start"] = base_meta.get("chunk_start", 0) + start
                chunks.append((chunk_text, meta))
            start += chunk_size - chunk_overlap
        return chunks

    @staticmethod
    def _split_markdown(
        content: str,
        source_file: str,
        chunk_size: int = 1500,
        chunk_overlap: int = 200,
    ) -> list[tuple[str, dict[str, Any]]]:
        """按标题层级切分 Markdown"""
        heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

        sections: list[dict[str, Any]] = []
        for m in heading_pattern.finditer(content):
            sections.append(
                {
                    "level": len(m.group(1)),
                    "title": m.group(2).strip(),
                    "start": m.end(),
                }
            )

        if not sections:
            return MemoryStore._chunk_long_text(
                content,
                chunk_size,
                chunk_overlap,
                {"source_file": source_file, "section_level": 0},
            )

        # 分配结束位置
        for i, section in enumerate(sections):
            if i + 1 < len(sections):
                next_h = heading_pattern.search(content, section["start"])
                section["end"] = next_h.start() if next_h else len(content)
            else:
                section["end"] = len(content)

        breadcrumbs: list[str] = []
        result: list[tuple[str, dict[str, Any]]] = []

        for section in sections:
            while breadcrumbs and len(breadcrumbs) >= section["level"]:
                breadcrumbs.pop()
            breadcrumbs.append(section["title"])
            full_title = " > ".join(breadcrumbs)

            text = content[section["start"] : section["end"]].strip()
            if not text:
                continue

            base_meta = {
                "source_file": source_file,
                "section_title": full_title,
                "section_level": section["level"],
                "category": breadcrumbs[0] if breadcrumbs else "",
                "chunk_start": section["start"],
            }

            if len(text) > chunk_size:
                result.extend(
                    MemoryStore._chunk_long_text(
                        text, chunk_size, chunk_overlap, base_meta
                    )
                )
            else:
                result.append((text, base_meta))

        return result
