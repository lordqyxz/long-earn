"""运动层 — 施加在物质上的运算，不持久化，只产出新物质或变更状态。

activate: WorldInfo 激活引擎（关键词触发 + 递归激活 + 预算控制）
decay: 按 form 配不同半衰期的衰减
detect_conflicts: 可配置词库的冲突检测
compress: 修复聚类算法的记忆压缩
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from loguru import logger

from long_earn.substance.model import Substance, SubstanceForm
from long_earn.substance.store import SubstanceStore

# ── 默认半衰期映射（按 form 配不同半衰期）────────────────────
DEFAULT_HALF_LIFE_MAP: dict[SubstanceForm, float] = {
    SubstanceForm.EVENT: 7.0,  # 新闻短半衰期
    SubstanceForm.KNOWLEDGE: 365.0,  # 知识长半衰期
    SubstanceForm.STRATEGY: 180.0,  # 策略中等
    SubstanceForm.BACKTEST: 90.0,  # 回测结果中等
    SubstanceForm.RELATION: 365.0,  # 关系长期
}

# 默认冲突检测词库（可配置，不再硬编码 16 词）
DEFAULT_CONFLICT_WORDS: dict[str, set[str]] = {
    "positive": {"利好", "上涨", "买入", "推荐", "增长", "提升", "优秀", "看好"},
    "negative": {"利空", "下跌", "卖出", "减持", "下降", "恶化", "风险", "看空"},
}

_CONTRADICT_THRESHOLD = 3
DECAY_THRESHOLD = 0.3
COMPRESS_SIMILARITY_THRESHOLD = 0.6
_MIN_CLUSTER_SIZE = 2


def _keyword_hit(substance: Substance, text: str) -> bool:
    """判断文本是否命中物质的关键词。"""
    return any(key in text for key in substance.keys)


def _passes_filter_logic(substance: Substance, text: str) -> bool:
    """应用 WorldInfo filter_keys 过滤逻辑。"""
    if not substance.filter_keys:
        return True
    matches = [fk in text for fk in substance.filter_keys]
    match substance.filter_logic:
        case "and_any":
            return any(matches)
        case "and_all":
            return all(matches)
        case "not_any":
            return not any(matches)
        case "not_all":
            return not all(matches)
    return True


def _activate_first_round(
    store: SubstanceStore, text: str, when: datetime
) -> dict[str, Substance]:
    """第一轮激活：关键词直接命中。"""
    activated: dict[str, Substance] = {}
    for s in store.get_all():
        if not s.is_visible_at(when) or not s.keys:
            continue
        if not _keyword_hit(s, text):
            continue
        if not _passes_filter_logic(s, text):
            continue
        activated[s.sid] = s
    return activated


def _activate_recursive(
    activated: dict[str, Substance],
    store: SubstanceStore,
    max_recursion: int,
    when: datetime,
) -> dict[str, Substance]:
    """递归激活：已激活物质的内容再激活其他物质。"""
    for _ in range(max_recursion):
        newly: dict[str, Substance] = {}
        for s in activated.values():
            for candidate in store.get_all():
                if candidate.sid in activated or candidate.sid in newly:
                    continue
                if not candidate.is_visible_at(when) or not candidate.keys:
                    continue
                if _keyword_hit(candidate, s.content):
                    newly[candidate.sid] = candidate
        if not newly:
            break
        activated.update(newly)
    return activated


def _resolve_conflict_groups(activated: dict[str, Substance]) -> dict[str, Substance]:
    """conflict_group 互斥：同组取 insertion_order 最高者。"""
    groups: dict[str, Substance] = {}
    to_remove: set[str] = set()
    for sid, s in activated.items():
        if not s.conflict_group:
            continue
        existing = groups.get(s.conflict_group)
        if existing is None:
            groups[s.conflict_group] = s
        elif s.insertion_order > existing.insertion_order:
            to_remove.add(existing.sid)
            groups[s.conflict_group] = s
        else:
            to_remove.add(sid)
    for sid in to_remove:
        activated.pop(sid, None)
    return activated


def activate(
    text: str,
    store: SubstanceStore,
    budget: int = 2000,
    max_recursion: int = 3,
    visible_at: datetime | None = None,
) -> list[Substance]:
    """WorldInfo 激活引擎 — 关键词触发 → filter_logic 过滤 → conflict_group 互斥 → 递归扫描 → 预算截断。

    Args:
        text: 输入文本（如用户查询或新闻事件）
        store: 物质存储
        budget: token 预算（返回物质数上限）
        max_recursion: 递归激活深度
        visible_at: 时间过滤时刻

    Returns:
        激活的物质列表（按 insertion_order 降序）
    """
    when = visible_at or datetime.now()
    store._ensure_index()

    activated = _activate_first_round(store, text, when)
    activated = _activate_recursive(activated, store, max_recursion, when)
    activated = _resolve_conflict_groups(activated)

    sorted_substances = sorted(
        activated.values(), key=lambda x: x.insertion_order, reverse=True
    )
    result = sorted_substances[:budget]
    logger.debug(f"激活 {len(result)} 条物质 (候选 {len(activated)})")
    return result


def decay(
    store: SubstanceStore,
    half_life_map: dict[SubstanceForm, float] | None = None,
) -> int:
    """记忆衰减 — 按 form 配不同半衰期，标记低衰减物质。

    Args:
        store: 物质存储
        half_life_map: form → 半衰期映射，None 用默认

    Returns:
        标记为已衰减的物质数
    """
    hl_map = half_life_map or DEFAULT_HALF_LIFE_MAP
    now = datetime.now()
    decayed_count = 0

    for s in store.get_all():
        half_life = hl_map.get(s.form, DEFAULT_HALF_LIFE_MAP[SubstanceForm.KNOWLEDGE])
        age_days = (now - s.created_at).total_seconds() / 86400.0
        factor = (
            1.0
            if age_days <= 0
            else math.exp(-0.6931471805599453 * age_days / half_life)
        )
        s.metadata["decay_factor"] = round(factor, 4)
        if factor < DECAY_THRESHOLD:
            s.metadata["decayed"] = True
            decayed_count += 1
        else:
            s.metadata["decayed"] = False

    logger.info(f"记忆衰减完成: {decayed_count}/{store.count} 条已衰减")
    return decayed_count


def detect_conflicts(
    store: SubstanceStore,
    substance: Substance,
    conflict_words: dict[str, set[str]] | None = None,
    min_similarity: float = 0.7,
) -> list[dict[str, Any]]:
    """冲突检测 — 可配置词库 + 语义相似度。

    Args:
        store: 物质存储
        substance: 待检测的新物质
        conflict_words: 冲突词库 {positive: set, negative: set}
        min_similarity: 冲突相似度阈值

    Returns:
        可能冲突的物质列表（含 conflict_reason）
    """
    words = conflict_words or DEFAULT_CONFLICT_WORDS
    results = store.search(substance.content, k=10, min_similarity=min_similarity)

    conflicts: list[dict[str, Any]] = []
    for r in results:
        existing = None
        for s in store.get_all():
            if s.content == r["content"]:
                existing = s
                break
        if existing is None:
            continue

        # 显式冲突组
        if existing.conflict_group:
            conflicts.append(
                {
                    "content": existing.content,
                    "metadata": existing.metadata,
                    "similarity": r["similarity"],
                    "conflict_reason": f"与冲突组 [{existing.conflict_group}] 中的物质相似",
                }
            )
            continue

        # 词库矛盾检测
        if _is_contradictory(substance.content, existing.content, words):
            conflicts.append(
                {
                    "content": existing.content,
                    "metadata": existing.metadata,
                    "similarity": r["similarity"],
                    "conflict_reason": "观点可能存在矛盾",
                }
            )

    return conflicts


def _is_contradictory(a: str, b: str, words: dict[str, set[str]]) -> bool:
    """基于可配置词库判断两段文本是否矛盾。"""
    positive = words.get("positive", set())
    negative = words.get("negative", set())

    def _score(text: str) -> float:
        pos = sum(1 for w in positive if w in text)
        neg = sum(1 for w in negative if w in text)
        return pos - neg

    return abs(_score(a) - _score(b)) >= _CONTRADICT_THRESHOLD


def compress(
    store: SubstanceStore,
    min_similarity: float = COMPRESS_SIMILARITY_THRESHOLD,
) -> int:
    """记忆压缩 — 修复聚类算法，合并高相似物质。

    Args:
        store: 物质存储
        min_similarity: 聚类相似度阈值

    Returns:
        合并后减少的物质数
    """
    store._ensure_index()
    substances = store.get_all()
    if len(substances) < _MIN_CLUSTER_SIZE:
        return 0

    # 用 TF-IDF 矩阵计算相似度
    retrieval = store._retrieval
    if (
        retrieval._semantic._doc_matrix is None
        or retrieval._semantic._doc_matrix.size == 0
    ):
        return 0

    doc_matrix = retrieval._semantic._doc_matrix
    sim_matrix = doc_matrix @ doc_matrix.T

    # 贪心聚类
    n = len(substances)
    merged: set[int] = set()
    clusters: list[list[int]] = []

    for i in range(n):
        if i in merged:
            continue
        cluster = [i]
        for j in range(i + 1, n):
            if j in merged:
                continue
            if sim_matrix[i, j] >= min_similarity:
                cluster.append(j)
                merged.add(j)
        if len(cluster) > 1:
            clusters.append(cluster)

    total_removed = 0
    for cluster in clusters:
        total_removed += _merge_cluster(store, cluster)

    if total_removed > 0:
        logger.info(f"记忆压缩完成: {len(clusters)} 组, 减少 {total_removed} 条物质")

    return total_removed


def _merge_cluster(store: SubstanceStore, indices: list[int]) -> int:
    """合并一组相似物质，保留第一条，其余内容追加。

    Args:
        store: 物质存储
        indices: 相似物质的列表索引

    Returns:
        移除的物质数
    """
    if len(indices) <= 1:
        return 0

    substances = store.get_all()
    keep_idx = indices[0]
    keep = substances[keep_idx]

    merged_content = [keep.content]
    for idx in indices[1:]:
        s = substances[idx]
        if s.content not in merged_content:
            merged_content.append(s.content)
        # 合并元数据
        for key in ("term", "category", "source_file"):
            val = s.metadata.get(key)
            if val and val not in str(keep.metadata.get(key, "")):
                existing = keep.metadata.get(key, "")
                keep.metadata[key] = f"{existing},{val}" if existing else val

    keep.content = "\n\n---\n\n".join(merged_content)
    keep.metadata["compressed"] = True
    keep.metadata["merged_count"] = len(indices)
    keep.metadata.pop("decayed", None)

    # 移除其余物质（从后往前删以避免索引偏移）
    removed = 0
    for idx in sorted(indices[1:], reverse=True):
        s = store._substances.pop(idx)
        store._sid_to_index.pop(s.sid, None)
        removed += 1

    # 重建 sid 索引
    store._sid_to_index = {s.sid: idx for idx, s in enumerate(store._substances)}
    store._dirty = True
    return removed
