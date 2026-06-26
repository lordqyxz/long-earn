"""SubstanceStore — 统一存储 + 索引协调 + 时间过滤。

替代旧 MemoryStore，统一管理 Substance 的增删查和双索引维护。
对外提供与旧 MemoryStore.search() 兼容的返回格式（dict 含 content/metadata/similarity），
使 MemoryServiceImpl 委托时消费方零改动。
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from long_earn.substance.indices.graph import GraphIndex
from long_earn.substance.indices.retrieval import RetrievalIndex
from long_earn.substance.model import Substance, SubstanceForm
from long_earn.substance.persistence import load_jsonl, save_jsonl

# ── 默认参数 ─────────────────────────────────────────────────────
DEFAULT_DECAY_HALF_LIFE = 90.0
COMPRESS_SIMILARITY_THRESHOLD = 0.6
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

    # ── 物质管理 ──────────────────────────────────────────────

    def add(self, substance: Substance) -> str:
        """添加物质，返回 sid。"""
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
        # 把旧系统 metadata 中的 term/category/source_file 也存入 metadata
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

    # 旧 MemoryStore 兼容属性
    @property
    def fact_count(self) -> int:
        """旧兼容：物质总数（knowledge 形态计数）。"""
        return sum(1 for s in self._substances if s.form is SubstanceForm.KNOWLEDGE)

    # ── 检索 ──────────────────────────────────────────────────

    def _ensure_index(self) -> None:
        """确保索引是最新的。"""
        if not self._dirty and self._retrieval.substance_count > 0:
            return
        self._retrieval.rebuild(self._substances)
        self._dirty = False

    def search(  # noqa: PLR0913, PLR0912
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
        """搜索物质库 — 返回与旧 MemoryStore.search() 兼容的格式。

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
                decay = s.decay_factor(now)
                # 使用物质自身半衰期或传入参数
                if s.decay_half_life_days != half_life_days:
                    age_days = (now - s.created_at).total_seconds() / 86400.0
                    if age_days > 0:
                        decay = math.exp(
                            -0.6931471805599453 * age_days / half_life_days
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

    def search_as_strings(
        self,
        query: str,
        k: int = 3,
        **kwargs: Any,
    ) -> list[str]:
        """搜索并返回格式化字符串（兼容旧接口）。"""
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

    def save(self, path: str | Path) -> None:
        """保存到 JSONL 文件。"""
        save_jsonl(self._substances, path)

    def load(self, path: str | Path) -> bool:
        """从 JSONL 文件加载。

        旧系统用 .npz + .pkl，新系统用 .jsonl。
        如果传入旧路径（.npz），自动改查 .jsonl。
        """
        path = Path(path)
        # 兼容旧路径扩展名
        if path.suffix == ".npz":
            path = path.with_suffix(".jsonl")
        if not path.exists():
            logger.warning(f"物质文件不存在: {path}")
            return False

        self._substances = load_jsonl(path)
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

    # ── 文档加载（Markdown 标题感知切分）───────────────────────

    def load_markdown(
        self,
        file_path: str | Path,
        chunk_size: int = 1500,
        chunk_overlap: int = 200,
    ) -> int:
        """加载 Markdown 文件并按标题切分存入物质库。"""
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
