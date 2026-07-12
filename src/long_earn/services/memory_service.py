"""记忆服务实现 — 委托 SubstanceStore（物质-运动统一架构，ADR-007）。

MemoryService Protocol 4 方法（ADR-007 破坏性收窄）：
- search: 知识检索（格式化字符串）
- save_experience: 策略经验存取（StrategyExperience 值对象，结构化 metadata）
- search_experience: 策略经验检索（返回 list[StrategyExperience]，无 markdown 往返）
- initialize: 生命周期初始化
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from long_earn.services import LoggerService, MemoryService, StrategyExperience
from long_earn.substance.model import Substance, SubstanceForm
from long_earn.substance.motion import activate as activate_substances
from long_earn.substance.store import SubstanceStore

if TYPE_CHECKING:
    from long_earn.config import AppConfig


class MemoryServiceImpl(MemoryService):
    """记忆服务 — 委托 SubstanceStore 实现 Protocol 契约。"""

    def __init__(self, config: "AppConfig", logger: LoggerService):
        self.config = config
        self.logger = logger
        self._store = SubstanceStore()
        self._initialized = False
        self._persistent_path: Path | None = None

    def initialize(self) -> None:
        """初始化记忆系统（加载持久化 DuckDB 或从 init 目录构建）。"""
        if self._initialized:
            return

        persistent_path = Path(self.config.memory_path).expanduser()
        self._persistent_path = persistent_path
        # 绑定持久化路径：之后每次 add 自动原子追加到 DuckDB
        self._store.bind_persistence(persistent_path)
        if persistent_path.exists() and self._store.load(persistent_path):
            self._initialized = True
            self.logger.info(f"记忆已加载 ({self._store.count} 条物质)")
            return

        init_dir = Path(self.config.init_dir)
        if init_dir.exists():
            count = self._store.load_directory(init_dir)
            if count > 0:
                persistent_path.parent.mkdir(parents=True, exist_ok=True)
                self._store.save(persistent_path)
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

        委托 ``motion.activate`` 做关键词触发 + 递归激活 + conflict_group 互斥，
        然后把命中的 EVENT/RELATION 物质格式化为带元数据头的字符串。
        """
        if not query.strip():
            return []

        substances = activate_substances(query, self._store, budget=k * 3 if include_relations else k)

        # 关系物质无 keys，motion.activate 不会直接命中；
        # include_relations=True 时，通过 source_id 关联补充已激活事件的关系
        extra_relations: list[Substance] = []
        if include_relations:
            event_sids = {s.sid for s in substances if s.form == SubstanceForm.EVENT}
            activated_sids = {s.sid for s in substances}
            for s in self._store.get_all():
                if (
                    s.form is SubstanceForm.RELATION
                    and s.sid not in activated_sids
                    and s.source_id in event_sids
                ):
                    extra_relations.append(s)

        output: list[str] = []
        for s in list(substances) + extra_relations:
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
                output.append(f"{header}\n{s.content}\n")
            elif s.form == SubstanceForm.RELATION and include_relations:
                meta = s.metadata
                target = meta.get("target", s.target_id or "")
                direction = meta.get("direction", "neutral")
                rel_type = s.relation_type or "impacts"
                header = f"【影响关系 | {rel_type} → {target} | 方向: {direction}"
                header += f" | 置信度: {s.confidence:.2f}】"
                output.append(f"{header}\n{s.content}\n")

            if len(output) >= k:
                break

        if output:
            self.logger.debug(f"激活事件上下文: query={query!r} → {len(output)} 条")
        return output

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

    def _auto_save(self) -> None:
        """[已废弃] 自动持久化 — add 已在绑定时自动追加，保留为未绑定场景兜底。"""
        if self._persistent_path is None:
            return
        try:
            self._store.save(self._persistent_path)
        except Exception as e:
            self.logger.warning(f"记忆自动保存失败: {e}")
