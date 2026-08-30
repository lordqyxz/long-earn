"""SubstanceStore — 统一存储 + 索引协调 + 时间过滤。

管理 Substance 的增删查和双索引维护。
返回 dict 含 content/metadata/similarity，供 MemoryServiceImpl 委托使用。
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

from long_earn.substance.indices.graph import GraphIndex
from long_earn.substance.indices.retrieval import RetrievalIndex
from long_earn.substance.model import Substance, SubstanceForm
from long_earn.substance.persistence import (
    delete_substance,
    load_all,
    save_many,
    save_substance,
)

# ── 默认参数 ─────────────────────────────────────────────────────
DEFAULT_DECAY_HALF_LIFE = 90.0
COMPRESS_SIMILARITY_THRESHOLD = 0.6


def _validate_chunk_params(chunk_size: int, chunk_overlap: int) -> None:
    """防止 chunk_overlap >= chunk_size 时步进为零导致死循环。"""
    if chunk_overlap >= chunk_size:
        raise ValueError(
            f"chunk_overlap ({chunk_overlap}) 必须小于 chunk_size ({chunk_size})"
        )
DECAY_THRESHOLD = 0.3
_MIN_CLUSTER_SIZE = 2


class SubstanceStore:
    """物质统一存储 — 管理物质生命周期和双索引。

    Usage:
        store = SubstanceStore()
        store.add(Substance(form=SubstanceForm.KNOWLEDGE, content="夏普比率衡量..."))
        results = store.search("风险调整收益", k=3)
    """

    def __init__(self, alpha: float = 0.5) -> None:
        self._substances: list[Substance] = []
        self._sid_to_index: dict[str, int] = {}
        self._retrieval = RetrievalIndex(alpha=alpha)
        self._graph = GraphIndex()
        self._dirty = True
        self._persist_bound = False

    # ── 物质管理 ──────────────────────────────────────────────

    def add(self, substance: Substance) -> str:
        """添加物质，返回 sid。若已绑定持久化路径，原子追加到 DuckDB。"""
        idx = len(self._substances)
        self._substances.append(substance)
        self._sid_to_index[substance.sid] = idx

        # 增量更新关键词索引
        for key in substance.keys:
            self._retrieval._keyword_index[key].append(substance.sid)

        # relation 形态同步更新图索引
        if substance.form is SubstanceForm.RELATION and substance.source_id:
            self._graph.add_edge(
                substance.source_id,
                substance.target_id or "",
                relation_sid=substance.sid,
                weight=substance.confidence,
            )

        self._dirty = True

        # 原子追加到 PostgreSQL（O(log n)），不再全量重写
        if self._persist_bound:
            save_substance(substance)
        return substance.sid

    def add_knowledge(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
        keys: list[str] | None = None,
    ) -> str:
        """便捷方法：添加 knowledge 形态物质，返回 sid。"""
        meta = metadata or {}
        s = Substance(
            form=SubstanceForm.KNOWLEDGE,
            content=content,
            keys=keys or [],
            metadata=meta,
        )
        return self.add(s)

    def get_by_sid(self, sid: str) -> Substance | None:
        """按 sid 获取物质。"""
        idx = self._sid_to_index.get(sid)
        if idx is None:
            return None
        return self._substances[idx]

    def get_all(self) -> list[Substance]:
        """获取所有物质。"""
        return list(self._substances)

    @property
    def count(self) -> int:
        """物质总数。"""
        return len(self._substances)

    # ── 检索 ──────────────────────────────────────────────────

    def _ensure_index(self) -> None:
        """确保索引是最新的。"""
        if not self._dirty and self._retrieval.substance_count > 0:
            return
        self._retrieval.rebuild(self._substances)
        self._dirty = False

    def search(  # noqa: PLR0913
        self,
        query: str,
        k: int = 3,
        categories: list[str] | None = None,
        terms: list[str] | None = None,
        source_files: list[str] | None = None,
        min_similarity: float = 0.0,
        apply_decay: bool = False,
        half_life_days: float = DEFAULT_DECAY_HALF_LIFE,
        include_decayed: bool = True,
        visible_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """搜索物质库。

        Args:
            query: 搜索查询
            k: 返回结果数量
            categories: 按类别过滤（metadata.category）
            terms: 按词条名称过滤（metadata.term）
            source_files: 按源文件过滤（metadata.source_file）
            min_similarity: 最小相似度阈值
            apply_decay: 是否应用时间衰减
            half_life_days: 衰减半衰期（天）
            include_decayed: 是否包含已衰减的结果
            visible_at: 时间过滤时刻（防未来函数）

        Returns:
            [{content, metadata, similarity}, ...]
        """
        self._ensure_index()
        now = visible_at or datetime.now()
        raw = self._retrieval.search(query, k=k * 3, visible_at=visible_at)

        results: list[dict[str, Any]] = []
        for r in raw:
            sid = r["sid"]
            s = self.get_by_sid(sid)
            if s is None:
                continue

            meta = s.metadata

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

            score = r["similarity"]

            # 时间衰减
            if apply_decay:
                # 物质自身半衰期优先（已设置且 >0），否则回退传入参数
                # （>0 守卫同时杜绝半衰期为 0 的除零风险）
                half_life = (
                    s.decay_half_life_days
                    if s.decay_half_life_days > 0
                    else half_life_days
                )
                age_days = (now - s.created_at).total_seconds() / 86400.0
                decay = (
                    1.0
                    if age_days <= 0
                    else math.exp(-0.6931471805599453 * age_days / half_life)
                )
                score *= decay
                if not include_decayed and decay < DECAY_THRESHOLD:
                    continue

            if score < min_similarity:
                continue

            results.append(
                {
                    "content": s.content,
                    "metadata": meta,
                    "similarity": float(score),
                }
            )
            if len(results) >= k:
                break

        return results

    def search_substances(
        self,
        query: str,
        k: int = 3,
        min_similarity: float = 0.0,
        visible_at: datetime | None = None,
    ) -> list[tuple[Substance, float]]:
        """搜索物质库并返回 (Substance, similarity) 对（按 sid 解析，避免 content 反查）。"""
        self._ensure_index()
        raw = self._retrieval.search(query, k=k * 3, visible_at=visible_at)
        hits: list[tuple[Substance, float]] = []
        for r in raw:
            sid = r["sid"]
            substance = self.get_by_sid(sid)
            if substance is None:
                continue
            score = float(r["similarity"])
            if score < min_similarity:
                continue
            hits.append((substance, score))
            if len(hits) >= k:
                break
        return hits

    def document_similarity_matrix(
        self,
    ) -> tuple[list[Substance], np.ndarray] | None:
        """返回物质列表与对应的文档余弦相似度矩阵（行序与列表一致）。"""
        self._ensure_index()
        pairwise = self._retrieval.pairwise_cosine_similarity()
        if pairwise is None:
            return None
        sid_order, sim_matrix = pairwise
        substances: list[Substance] = []
        for sid in sid_order:
            substance = self.get_by_sid(sid)
            if substance is not None:
                substances.append(substance)
        if len(substances) != sim_matrix.shape[0]:
            return None
        return substances, sim_matrix

    def search_as_strings(
        self,
        query: str,
        k: int = 3,
        **kwargs: Any,
    ) -> list[str]:
        """搜索并返回格式化字符串。"""
        results = self.search(query, k=k, **kwargs)
        output: list[str] = []
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

    def add_relation(
        self,
        source: str,
        target: str,
        weight: float = 1.0,
        relation_type: str = "related_to",
    ) -> str:
        """添加关系物质（关系是一等物质，有完整 provenance）。"""
        s = Substance(
            form=SubstanceForm.RELATION,
            content=f"{source} --[{relation_type}]--> {target}",
            source_id=source,
            target_id=target,
            relation_type=relation_type,
            confidence=weight,
        )
        return self.add(s)

    def get_related(self, entity_id: str, depth: int = 2) -> list[str]:
        """获取关联实体（BFS）。"""
        return [r["sid"] for r in self._graph.bfs(entity_id, max_depth=depth)]

    @property
    def graph(self) -> GraphIndex:
        """暴露图索引（供 motion 层使用）。"""
        return self._graph

    # ── 持久化 ────────────────────────────────────────────────

    def save(self) -> None:
        """全量同步到 PostgreSQL（批量原子写入）。

        适用于初始化导入、compress 批量变更后的落盘。
        日常 add 已在 ``add()`` 内原子追加，无需调本方法。
        """
        save_many(self._substances)
        self._persist_bound = True

    def load(self) -> bool:
        """从 PostgreSQL 加载全部物质到内存热存储。

        Returns:
            是否成功加载（PG 中有物质）
        """
        self._substances = load_all()
        self._sid_to_index = {s.sid: idx for idx, s in enumerate(self._substances)}
        # 重建图索引
        for s in self._substances:
            if s.form is SubstanceForm.RELATION and s.source_id:
                self._graph.add_edge(
                    s.source_id,
                    s.target_id or "",
                    relation_sid=s.sid,
                    weight=s.confidence,
                )
        self._dirty = True
        return len(self._substances) > 0

    def bind_persistence(self) -> None:
        """绑定持久化 — 之后每次 ``add`` 自动原子追加到 PostgreSQL。"""
        self._persist_bound = True

    def update(self, substance: Substance) -> None:
        """同步单条物质的内存变更到 PostgreSQL（UPSERT 整行覆盖）。

        物质对象与内存热存储共享引用，content/metadata 变更即时生效；
        本方法负责把变更落盘，与 ``add`` 的原子追加、``remove`` 的即时
        删除对称（如 compress 合并后的聚合内容）。未绑定持久化时仅
        失效检索索引，不写库。

        Args:
            substance: 已存在于本存储中的物质（须与内存对象同一实例）

        Raises:
            ValueError: 物质不在本存储中（防止 UPSERT 出内存不感知的行）
        """
        if substance.sid not in self._sid_to_index:
            raise ValueError(f"物质不在存储中，无法同步变更: {substance.sid}")
        self._dirty = True
        if self._persist_bound:
            save_substance(substance)

    def remove(self, sid: str) -> bool:
        """删除物质（motion.compress 压缩后调用）。

        先删除 PostgreSQL 行，成功后才移除内存与索引，保证两层状态一致；
        持久层删除失败时内存保持不动并告警。

        Returns:
            是否删除成功
        """
        idx = self._sid_to_index.get(sid)
        if idx is None:
            return False
        # 先删持久层，成功才动内存（避免 PG 删除失败后内存与 PG 不一致）
        if self._persist_bound and not delete_substance(sid):
            logger.warning(f"物质删除失败（PostgreSQL 删除未成功）: {sid}")
            return False
        self._substances.pop(idx)
        # 重建索引映射（pop 后下标偏移）
        self._sid_to_index = {sub.sid: i for i, sub in enumerate(self._substances)}
        self._dirty = True
        return True

    # ── 文档加载（Markdown 标题感知切分）───────────────────────

    def load_markdown(
        self,
        file_path: str | Path,
        chunk_size: int = 1500,
        chunk_overlap: int = 200,
    ) -> int:
        """加载 Markdown 文件并按标题切分存入物质库。"""
        _validate_chunk_params(chunk_size, chunk_overlap)
        file_path = Path(file_path)
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"读取文件失败 {file_path}: {e}")
            return 0

        chunks = _split_markdown(content, file_path.name, chunk_size, chunk_overlap)
        for chunk_text, meta in chunks:
            self.add_knowledge(chunk_text, metadata=meta)

        logger.info(f"已加载 {file_path.name}: {len(chunks)} 个切片")
        return len(chunks)

    def load_text(
        self,
        file_path: str | Path,
        chunk_size: int = 1500,
        chunk_overlap: int = 200,
    ) -> int:
        """加载纯文本文件并切分。"""
        _validate_chunk_params(chunk_size, chunk_overlap)
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
                self.add_knowledge(
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
        """加载目录中的所有支持文件。"""
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

        logger.info(f"目录加载完成: {directory} ({total} 条物质)")
        return total


def _chunk_long_text(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
    base_meta: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """将长文本切分为重叠的固定大小片段。"""
    _validate_chunk_params(chunk_size, chunk_overlap)
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


def _split_markdown(
    content: str,
    source_file: str,
    chunk_size: int = 1500,
    chunk_overlap: int = 200,
) -> list[tuple[str, dict[str, Any]]]:
    """按标题层级切分 Markdown。"""
    _validate_chunk_params(chunk_size, chunk_overlap)
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
        return _chunk_long_text(
            content,
            chunk_size,
            chunk_overlap,
            {"source_file": source_file, "section_level": 0},
        )

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
            result.extend(_chunk_long_text(text, chunk_size, chunk_overlap, base_meta))
        else:
            result.append((text, base_meta))

    return result
