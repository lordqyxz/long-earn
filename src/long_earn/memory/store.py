"""记忆存储 — 综合事实 + 向量检索 + 关系图的统一记忆系统

基于 numpy/pandas 技术底座，无外部向量数据库依赖。

功能：
- 事实管理（增删改查、持久化）
- TF-IDF 语义检索（支持元数据过滤）
- 记忆衰减（按时间降低旧事实权重）
- 冲突检测（识别相互矛盾的记忆）
- 记忆压缩（合并相似事实降低冗余）

v2.0 新增：记忆衰减、冲突检测、记忆压缩
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

# ── 时间常量 ─────────────────────────────────────────────────────
SECONDS_PER_DAY = 86400.0
DEFAULT_DECAY_HALF_LIFE = 90.0  # 默认衰减半衰期（天）
CONFLICT_SIMILARITY_THRESHOLD = 0.7  # 冲突检测的相似度阈值
DECAY_THRESHOLD = 0.3  # 衰减判定阈值（decay < 0.3 视为已衰减）
COMPRESS_SIMILARITY_THRESHOLD = 0.6  # 压缩聚类的相似度阈值
_CONTRADICT_THRESHOLD = 3  # 矛盾判定阈值
_MIN_CLUSTER_SIZE = 2  # 压缩最小聚类大小


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

    # ── 记忆衰减 ──────────────────────────────────────────────

    @staticmethod
    def _calc_decay(
        created_at: str,
        now: datetime | None = None,
        half_life_days: float = DEFAULT_DECAY_HALF_LIFE,
    ) -> float:
        """计算时间衰减因子

        使用指数衰减曲线: decay = exp(-age_days / half_life_days)
        半衰期为 half_life_days 天后，衰减因子降至 0.5。

        Args:
            created_at: ISO 格式创建时间
            now: 当前时间（默认 datetime.now()）
            half_life_days: 衰减半衰期（天）

        Returns:
            衰减因子 [0, 1]
        """
        if now is None:
            now = datetime.now()
        try:
            created = datetime.fromisoformat(created_at)
            age = (now - created).total_seconds()
            age_days = age / SECONDS_PER_DAY
            if age_days <= 0:
                return 1.0
            return float(np.exp(-age_days / half_life_days))
        except (ValueError, TypeError):
            return 1.0

    def decay(self, half_life_days: float = DEFAULT_DECAY_HALF_LIFE) -> int:
        """对所有事实执行记忆衰减

        过旧的事实会被标记为 `decayed=True`，降低检索权重。

        Args:
            half_life_days: 衰减半衰期（天）

        Returns:
            衰减的事实数量（decay < 0.3 的视为已衰减）
        """
        now = datetime.now()
        decayed_count = 0
        for fact in self._facts:
            created_at = fact["metadata"].get("created_at", "")
            decay_factor = self._calc_decay(created_at, now, half_life_days)
            fact["metadata"]["decay_factor"] = round(decay_factor, 4)
            if decay_factor < DECAY_THRESHOLD:
                fact["metadata"]["decayed"] = True
                decayed_count += 1
            else:
                fact["metadata"]["decayed"] = False
        self._dirty = True
        logger.info(f"记忆衰减完成: {decayed_count}/{self.fact_count} 条已衰减")
        return decayed_count

    # ── 冲突检测 ──────────────────────────────────────────────

    def find_conflicts(
        self,
        content: str,
        min_similarity: float = CONFLICT_SIMILARITY_THRESHOLD,
    ) -> list[dict[str, Any]]:
        """检测新内容与现有记忆的潜在冲突

        通过 TF-IDF 余弦相似度查找高相关事实，
        并对比核心元数据判断是否存在冲突。

        Args:
            content: 待检测的文本内容
            min_similarity: 冲突相似度阈值

        Returns:
            可能存在冲突的事实列表（含 similarity 和 conflict_reason）
        """
        self._ensure_vectors()
        if self._doc_matrix is None or self._doc_matrix.size == 0:
            return []

        query_vec = self._vectorizer.transform([content])[0]
        similarities = cosine_similarity(query_vec, self._doc_matrix)

        conflicts: list[dict[str, Any]] = []
        for idx, score in enumerate(similarities):
            if score < min_similarity:
                continue
            fact = self._facts[idx]
            meta = fact["metadata"]

            # 检查是否有显式冲突标记
            conflict_group = meta.get("conflict_group", "")
            if conflict_group:
                conflicts.append(
                    {
                        "content": fact["content"],
                        "metadata": meta,
                        "similarity": float(score),
                        "conflict_reason": f"与冲突组 [{conflict_group}] 中的记忆相似",
                    }
                )
                continue

            # 检查同一词条下的相反观点
            term = meta.get("term", "")
            if term and self._is_contradictory(content, fact["content"]):
                conflicts.append(
                    {
                        "content": fact["content"],
                        "metadata": meta,
                        "similarity": float(score),
                        "conflict_reason": f"关于 [{term}] 的观点可能存在矛盾",
                    }
                )

        return conflicts

    @staticmethod
    def _is_contradictory(a: str, b: str) -> bool:
        """粗略判断两段文本是否存在矛盾

        基于正负面关键词的简单对比。
        """
        positive = {"利好", "上涨", "买入", "推荐", "增长", "提升", "优秀", "看好"}
        negative = {"利空", "下跌", "卖出", "减持", "下降", "恶化", "风险", "看空"}

        def _sentiment_score(text: str) -> float:
            pos = sum(1 for w in positive if w in text)
            neg = sum(1 for w in negative if w in text)
            return pos - neg

        score_a = _sentiment_score(a)
        score_b = _sentiment_score(b)
        return abs(score_a - score_b) >= _CONTRADICT_THRESHOLD

    def resolve_conflict(
        self,
        existing_idx: int,
        new_content: str,
        new_metadata: dict[str, Any] | None = None,
    ) -> int:
        """解决冲突：将冲突记忆编入同一冲突组

        在冲突事实之间建立 `conflict_group` 关联，
        确保后续检索时双方都被返回。

        Args:
            existing_idx: 已有事实索引
            new_content: 新事实内容
            new_metadata: 新事实元数据

        Returns:
            新事实索引
        """
        # 获取已有事实的冲突组 ID
        existing = self._facts[existing_idx]
        conflict_group = existing["metadata"].get("conflict_group", "")

        if not conflict_group:
            # 从已有事实的 term 创建冲突组
            term = existing["metadata"].get("term", "unknown")
            conflict_group = f"conflict_{term}_{existing_idx}"

        # 标记已有事实
        existing["metadata"]["conflict_group"] = conflict_group
        existing["metadata"]["conflict_version"] = existing["metadata"].get(
            "conflict_version", 1
        )

        # 添加新事实并标记冲突
        meta = dict(new_metadata or {})
        meta["conflict_group"] = conflict_group
        meta["conflict_version"] = existing["metadata"].get("conflict_version", 0) + 1
        meta["is_conflict_entry"] = True

        self._facts.append({"content": new_content, "metadata": meta})
        self._fact_texts.append(new_content)
        self._dirty = True
        logger.info(f"冲突已标记，冲突组: {conflict_group}")
        return len(self._facts) - 1

    # ── 记忆压缩与总结 ────────────────────────────────────────

    def compress(
        self,
        min_similarity: float = COMPRESS_SIMILARITY_THRESHOLD,
        max_cluster_size: int = 10,
    ) -> int:
        """压缩冗余事实

        将语义高度相似的事实聚类，每组只保留一份摘要。

        Args:
            min_similarity: 聚类相似度阈值
            max_cluster_size: 每个聚类最多允许的事实数

        Returns:
            合并后减少的事实数
        """
        self._ensure_vectors()
        if self._doc_matrix is None or self._doc_matrix.size < _MIN_CLUSTER_SIZE:
            return 0

        # 计算文档间相似度矩阵
        n = len(self._facts)
        sim_matrix = self._doc_matrix @ self._doc_matrix.T

        # 贪心聚类：找到相似的文档对并合并
        merged_indices: set[int] = set()
        clusters: list[list[int]] = []

        for i in range(n):
            if i in merged_indices:
                continue
            cluster = [i]
            for j in range(i + 1, n):
                if j in merged_indices:
                    continue
                if len(cluster) >= max_cluster_size:
                    break
                if sim_matrix[i, j] >= min_similarity:
                    cluster.append(j)
                    merged_indices.add(j)

            if len(cluster) > 1:
                clusters.append(cluster)

        total_removed = 0
        for cluster in clusters:
            removed = self._merge_cluster(cluster)
            total_removed += removed

        if total_removed > 0:
            logger.info(
                f"记忆压缩完成: {len(clusters)} 组, 减少 {total_removed} 条事实"
            )

        return total_removed

    def _merge_cluster(self, indices: list[int]) -> int:
        """合并一组相似事实

        保留第一条作为主事实，将其余事实的关键信息追加到主事实。

        Args:
            indices: 相似事实的索引列表（含主事实）

        Returns:
            移除的事实数
        """
        if len(indices) <= 1:
            return 0

        # 主事实：索引最小的
        keep_idx = indices[0]
        keep_fact = self._facts[keep_idx]

        # 收集去重内容
        merged_content = [keep_fact["content"]]
        for idx in indices[1:]:
            fact = self._facts[idx]
            content = fact["content"]
            if content not in merged_content:
                merged_content.append(content)

            # 合并元数据中的 term 和 category
            meta = fact["metadata"]
            for key in ("term", "category", "source_file"):
                val = meta.get(key)
                if val and val not in str(keep_fact["metadata"].get(key, "")):
                    existing = keep_fact["metadata"].get(key, "")
                    keep_fact["metadata"][key] = (
                        f"{existing},{val}" if existing else val
                    )

        # 更新主事实
        separator = "\n\n---\n\n"
        keep_fact["content"] = separator.join(merged_content)
        keep_fact["metadata"]["compressed"] = True
        keep_fact["metadata"]["merged_count"] = len(indices)
        keep_fact["metadata"].pop("decayed", None)  # 如果被衰减，清除标记

        # 移除其余事实（从后往前删以避免索引偏移）
        removed = 0
        for idx in sorted(indices[1:], reverse=True):
            self._facts.pop(idx)
            self._fact_texts.pop(idx)
            removed += 1

        self._dirty = True
        return removed

    def summarize_topic(self, topic: str, k: int = 5) -> str:
        """生成某个主题的总结

        Args:
            topic: 主题关键词
            k: 检索相关事实数量

        Returns:
            主题总结文本
        """
        related = self.search(topic, k=k, min_similarity=0.1)
        if not related:
            return f"未找到关于「{topic}」的记忆"

        lines: list[str] = [f"# 主题总结：{topic}\n"]
        for i, r in enumerate(related, 1):
            content = r["content"][:300]
            meta = r["metadata"]
            created = meta.get("created_at", "unknown")[:10]
            source = meta.get("source_file", "unknown")
            lines.append(f"## {i}. [来源: {source} | {created}]")
            lines.append(content)
            lines.append("")

        return "\n".join(lines)

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

    def search(  # noqa: PLR0913, PLR0912
        self,
        query: str,
        k: int = 3,
        categories: list[str] | None = None,
        terms: list[str] | None = None,
        source_files: list[str] | None = None,
        min_similarity: float = 0.0,
        apply_decay: bool = True,
        half_life_days: float = DEFAULT_DECAY_HALF_LIFE,
        include_decayed: bool = False,
    ) -> list[dict[str, Any]]:
        """搜索记忆库

        Args:
            query: 搜索查询
            k: 返回结果数量
            categories: 按类别过滤
            terms: 按词条名称过滤
            source_files: 按源文件过滤
            min_similarity: 最小相似度阈值
            apply_decay: 是否应用时间衰减
            half_life_days: 衰减半衰期（天）
            include_decayed: 是否包含已衰减的结果

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

        # 应用时间衰减
        if apply_decay:
            now = datetime.now()
            for idx in range(len(similarities)):
                created_at = self._facts[idx]["metadata"].get("created_at", "")
                decay = self._calc_decay(created_at, now, half_life_days)
                similarities[idx] *= decay

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

            # 过滤已衰减的事实
            if not include_decayed and meta.get("decayed", False):
                continue

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
