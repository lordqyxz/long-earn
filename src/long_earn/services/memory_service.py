"""记忆服务实现 — 委托 SubstanceStore（物质-运动统一架构，ADR-007）。

MemoryService Protocol 4 方法（ADR-007 破坏性收窄）：
- search: 知识检索（格式化字符串）
- save_experience: 策略经验存取（StrategyExperience 值对象，结构化 metadata）
- search_experience: 策略经验检索（返回 list[StrategyExperience]，无 markdown 往返）
- initialize: 生命周期初始化
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from long_earn.ontology.model import RelationType
from long_earn.services import LoggerService, MemoryService, StrategyExperience
from long_earn.substance.model import Substance, SubstanceForm
from long_earn.substance.motion import activate as activate_substances
from long_earn.substance.store import SubstanceStore

if TYPE_CHECKING:
    from long_earn.config import AppConfig
    from long_earn.ontology.graph import OntologyGraph


class MemoryServiceImpl(MemoryService):
    """记忆服务 — 委托 SubstanceStore 实现 Protocol 契约。"""

    def __init__(
        self,
        config: AppConfig,
        logger: LoggerService,
        ontology_graph: OntologyGraph | None = None,
    ):
        self.config = config
        self.logger = logger
        self._store = SubstanceStore()
        self._initialized = False
        # ADR-014 阶段 D：可选注入 OntologyGraph，motion.activate 走图遍历
        self._ontology_graph = ontology_graph

    def initialize(self) -> None:
        """初始化记忆系统（加载 PostgreSQL 持久化或从 init 目录构建）。

        物质存储位于 PostgreSQL（core.pg 裁决连接参数）。优先从 PG
        加载既有物质；PG 为空时从 init 目录构建并落库。
        """
        if self._initialized:
            return

        self._store.bind_persistence()
        if self._store.load():
            self._initialized = True
            self.logger.info(f"记忆已加载 ({self._store.count} 条物质)")
            return

        init_dir = Path(self.config.init_dir)
        if init_dir.exists():
            count = self._store.load_directory(init_dir)
            if count > 0:
                self._store.save()
                self.logger.info(f"记忆初始化完成 ({count} 条事实)")

        self._initialized = True

    # ── 知识检索 ───────────────────────────────────────────────

    def search(
        self,
        query: str,
        k: int = 3,
        **filters: Any,
    ) -> list[str]:
        """检索知识片段，返回格式化字符串供 prompt 注入。"""
        results = self._store.search(query, k=k, **filters)
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

    # ── 策略经验 ───────────────────────────────────────────────

    def save_experience(self, experience: StrategyExperience) -> str:
        """保存策略经验 — 构造 STRATEGY 形态物质，字段存入结构化 metadata（无 markdown）。"""
        metrics = experience.metrics or {}
        s = Substance(
            form=SubstanceForm.STRATEGY,
            content=experience.rationale or experience.name,
            keys=[experience.name] if experience.name else [],
            metadata={
                "experience_type": "strategy",
                "term": experience.name,
                "category": "策略经验",
                "strategy_code": experience.code,
                "design_rationale": experience.rationale,
                "backtest_metrics": metrics,
                "reflection": experience.reflection,
                "error_history": experience.error_history or [],
                "sharpe_ratio": metrics.get("sharpe_ratio"),
                "backtest_success": not metrics.get("error"),
            },
        )
        sid = self._store.add(s)
        self.logger.debug(f"策略经验已存储: {experience.name} ({sid})")
        return sid

    def search_experience(
        self,
        query: str,
        k: int = 3,
        min_sharpe: float | None = None,
    ) -> list[StrategyExperience]:
        """搜索历史策略经验 — 从结构化 metadata 重建 StrategyExperience（无 regex）。"""
        try:
            results = self._store.search(
                query, k=k * 2, min_similarity=0.05, categories=["策略经验"]
            )
        except Exception as e:
            self.logger.error(f"搜索经验失败: {e}")
            return []

        experiences: list[StrategyExperience] = []
        for r in results:
            meta = r["metadata"]
            if meta.get("experience_type") != "strategy":
                continue

            if min_sharpe is not None:
                s = meta.get("sharpe_ratio")
                if s is None:
                    s = (meta.get("backtest_metrics", {}) or {}).get("sharpe_ratio")
                if s is None or s < min_sharpe:
                    continue

            experiences.append(
                StrategyExperience(
                    name=meta.get("term", ""),
                    code=meta.get("strategy_code", ""),
                    rationale=meta.get("design_rationale", ""),
                    metrics=meta.get("backtest_metrics", {}) or {},
                    reflection=meta.get("reflection", ""),
                    error_history=meta.get("error_history"),
                )
            )
            if len(experiences) >= k:
                break
        return experiences

    # ── 事件激活（ADR-007 Phase 3）────────────────────────────

    def activate_events(
        self,
        query: str,
        k: int = 5,
        include_relations: bool = True,
    ) -> list[str]:
        """WorldInfo 激活引擎 — 关键词触发事件/关系物质，返回 prompt 注入字符串。

        ADR-014 阶段 D：注入 OntologyGraph 时走图遍历激活（替代旧关键词递归），
        关系补充也用图遍历替代 O(N) store.get_all() 扫描。
        """
        if not query.strip():
            return []

        substances = activate_substances(
            query,
            self._store,
            budget=k * 3 if include_relations else k,
            graph=self._ontology_graph,
        )

        extra_relations = (
            self._collect_extra_relations(substances) if include_relations else []
        )

        output: list[str] = []
        for s in list(substances) + extra_relations:
            formatted = self._format_substance(s, include_relations)
            if formatted:
                output.append(formatted)
            if len(output) >= k:
                break

        if output:
            self.logger.debug(f"激活事件上下文: query={query!r} → {len(output)} 条")
        return output

    def _collect_extra_relations(self, substances: list[Substance]) -> list[Substance]:
        """补充已激活事件的关系物质。

        ADR-014 阶段 D：有 OntologyGraph 时用图遍历（O(图深度)），无则降级 O(N) 扫描。
        """
        event_sids = {s.sid for s in substances if s.form == SubstanceForm.EVENT}
        activated_sids = {s.sid for s in substances}
        if not event_sids:
            return []

        extra: list[Substance] = []
        if self._ontology_graph is not None:
            for event_sid in event_sids:
                paths = self._ontology_graph.traverse(
                    event_sid,
                    max_depth=1,
                    min_weight=0.0,
                    relation_types={RelationType.IMPACTS, RelationType.PROPAGATES_TO},
                    direction="forward",
                )
                for p in paths:
                    rel_sub = self._store.get_by_sid(p.sid)
                    if (
                        rel_sub is not None
                        and rel_sub.form is SubstanceForm.RELATION
                        and rel_sub.sid not in activated_sids
                    ):
                        extra.append(rel_sub)
        else:
            for s in self._store.get_all():
                if (
                    s.form is SubstanceForm.RELATION
                    and s.sid not in activated_sids
                    and s.source_id in event_sids
                ):
                    extra.append(s)
        return extra

    @staticmethod
    def _format_substance(s: Substance, include_relations: bool) -> str:
        """格式化单个物质为 prompt 注入字符串（带元数据头）。"""
        if s.form == SubstanceForm.EVENT:
            meta = s.metadata
            sentiment = meta.get("sentiment", "neutral")
            symbols = meta.get("symbols", []) or []
            category = meta.get("event_category", "")
            header = "【事件"
            if symbols:
                header += f" | 标的: {','.join(symbols)}"
            if sentiment and sentiment != "neutral":
                header += f" | 情绪: {sentiment}"
            if category:
                header += f" | 类别: {category}"
            header += f" | 置信度: {s.confidence:.2f}】"
            return f"{header}\n{s.content}\n"
        if s.form == SubstanceForm.RELATION and include_relations:
            meta = s.metadata
            target = meta.get("target", s.target_id or "")
            direction = meta.get("direction", "neutral")
            rel_type = s.relation_type or "impacts"
            header = f"【影响关系 | {rel_type} → {target} | 方向: {direction}"
            header += f" | 置信度: {s.confidence:.2f}】"
            return f"{header}\n{s.content}\n"
        return ""

    # ── 假设树摘要（ADR-010 Phase 4）──────────────────────────

    def save_hypothesis_tree(
        self,
        run_id: str,
        best_insight: str,
        best_direction: str,
        node_count: int,
    ) -> str:
        """保存假设树摘要为 knowledge Substance（category="研究树"）。"""
        s = Substance(
            form=SubstanceForm.KNOWLEDGE,
            content=best_insight or f"研究 {run_id} 无洞察",
            keys=[run_id, best_direction] if best_direction else [run_id],
            metadata={
                "experience_type": "hypothesis_tree",
                "category": "研究树",
                "term": run_id,
                "best_insight": best_insight,
                "best_direction": best_direction,
                "node_count": node_count,
            },
        )
        sid = self._store.add(s)
        self.logger.debug(f"假设树摘要已存储: {run_id} ({sid})")
        return sid

    def search_hypothesis_trees(
        self,
        query: str,
        k: int = 3,
    ) -> list[dict[str, Any]]:
        """检索历史假设树摘要（hot-start）。"""
        try:
            results = self._store.search(
                query, k=k * 2, min_similarity=0.05, categories=["研究树"]
            )
        except Exception as e:
            self.logger.error(f"搜索假设树摘要失败: {e}")
            return []

        trees: list[dict[str, Any]] = []
        for r in results:
            meta = r["metadata"]
            if meta.get("experience_type") != "hypothesis_tree":
                continue
            trees.append(
                {
                    "run_id": meta.get("term", ""),
                    "best_insight": meta.get("best_insight", ""),
                    "best_direction": meta.get("best_direction", ""),
                    "node_count": meta.get("node_count", 0),
                }
            )
            if len(trees) >= k:
                break
        return trees

    # ── 事件推理（ADR-007 Phase 2）────────────────────────────

    def save_events(
        self,
        events: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        conflict_groups: dict[int, str] | None = None,
    ) -> dict[str, Any]:
        """保存新闻事件 + 影响关系物质。

        events: [{content, keys, symbols, sentiment, category, confidence}, ...]
        relations: [{event_index, target, relation_type, confidence, direction, rationale}, ...]
        conflict_groups: {event_index: conflict_group_id}
        """
        conflict_groups = conflict_groups or {}
        event_sids: list[str] = []

        for idx, ev in enumerate(events):
            content = str(ev.get("content", "")).strip()
            if not content:
                event_sids.append("")
                continue
            symbols = ev.get("symbols") or []
            s = Substance(
                form=SubstanceForm.EVENT,
                content=content,
                keys=list(ev.get("keys") or []),
                source="event_inference",
                confidence=float(ev.get("confidence", 1.0)),
                conflict_group=conflict_groups.get(idx),
                metadata={
                    "category": "新闻事件",
                    "event_category": ev.get("category", ""),
                    "sentiment": ev.get("sentiment", "neutral"),
                    "symbols": symbols,
                },
            )
            event_sids.append(self._store.add(s))

        relation_sids: list[str] = []
        for rel in relations:
            idx = rel.get("event_index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(event_sids):
                continue
            source_sid = event_sids[idx]
            if not source_sid:
                continue
            target = str(rel.get("target", ""))
            rationale = str(rel.get("rationale", ""))
            s = Substance(
                form=SubstanceForm.RELATION,
                content=rationale or f"影响 {target}",
                source_id=source_sid,
                target_id=target,
                relation_type=str(rel.get("relation_type", "impacts")),
                confidence=float(rel.get("confidence", 0.5)),
                source="event_inference",
                metadata={
                    "direction": rel.get("direction", "neutral"),
                    "target": target,
                },
            )
            relation_sids.append(self._store.add(s))

        count = len([s for s in event_sids if s]) + len(relation_sids)
        self.logger.info(
            f"事件推理落库: {len(event_sids)} 事件 + {len(relation_sids)} 关系 ({count} 物质)"
        )
        return {
            "event_sids": event_sids,
            "relation_sids": relation_sids,
            "event_count": len([s for s in event_sids if s]),
            "relation_count": len(relation_sids),
        }

    # ── 内部 ───────────────────────────────────────────────────
