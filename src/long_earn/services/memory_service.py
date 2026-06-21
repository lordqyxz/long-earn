"""记忆服务实现 — 基于 MemoryStore 的 3-Tier 记忆系统

Working / Core / Archival 三级记忆，遵循 Letta/MemGPT 模式。
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from long_earn.memory.embedding import EmbeddingRetriever
from long_earn.memory.store import MemoryStore
from long_earn.services import LoggerService, MemoryService

if TYPE_CHECKING:
    from long_earn.config import AppConfig


class MemoryServiceImpl(MemoryService):
    """3-Tier 记忆服务"""

    # 语义检索默认融合权重（TF-IDF 0.5 + 嵌入 0.5）。
    # sentence-transformers（embed extra）未安装时自动回退到纯 TF-IDF，零行为变化。
    _DEFAULT_HYBRID_ALPHA: float = 0.5

    def __init__(self, config: "AppConfig", logger: LoggerService):
        self.config = config
        self.logger = logger
        self._store = MemoryStore()
        self._initialized = False
        # 嵌入检索器懒加载：仅当 embed extra 可用时才启用语义混合检索
        self._embedding: EmbeddingRetriever | None = None

    def initialize(self) -> None:
        if self._initialized:
            return

        persistent_path = Path(self.config.memory_path).expanduser()
        if persistent_path.exists() and self._store.load(persistent_path):
            self._initialized = True
            self.logger.info(f"记忆已加载 ({self._store.fact_count} 条)")
            return

        init_dir = Path(self.config.init_dir)
        if init_dir.exists():
            count = self._store.load_directory(init_dir)
            if count > 0:
                persistent_path.parent.mkdir(parents=True, exist_ok=True)
                self._store.save(persistent_path)
                self.logger.info(f"记忆初始化完成 ({count} 条事实)")

        self._initialized = True

    # ── Recall ─────────────────────────────────────────────────

    def recall(
        self,
        query: str,
        tier: str = "core",  # noqa: ARG002
        k: int = 3,
        **filters,
    ) -> list[dict[str, Any]]:
        """语义检索记忆

        检索策略：
        - 当 ``embed`` extra（sentence-transformers）可用时，使用 TF-IDF + 嵌入向量的
          混合检索（``EmbeddingRetriever.hybrid_search``），提升长文本与近义表达的匹配质量；
        - 否则回退到纯 TF-IDF 检索（``MemoryStore.search``），行为与未接入语义检索前一致。

        ``filters`` 中的 ``alpha``（0~1，越高越偏重语义）可覆盖默认融合权重。
        """
        try:
            retriever = self._get_embedding_retriever()
            if retriever is not None:
                alpha = float(filters.pop("alpha", self._DEFAULT_HYBRID_ALPHA))
                return retriever.hybrid_search(
                    self._store, query, k=k, alpha=alpha, **filters
                )
            return self._store.search(query, k=k, **filters)
        except Exception as e:
            self.logger.error(f"记忆检索失败: {e}")
            return []

    def _get_embedding_retriever(self) -> EmbeddingRetriever | None:
        """懒加载嵌入检索器；embed extra 不可用时返回 None（回退纯 TF-IDF）。"""
        if self._embedding is not None:
            return self._embedding
        retriever = EmbeddingRetriever()
        if retriever.is_available:
            self._embedding = retriever
            self.logger.info("语义嵌入检索已启用（TF-IDF + 嵌入混合检索）")
        return self._embedding

    # ── Remember ───────────────────────────────────────────────

    def remember(
        self,
        content: str,
        tier: str = "core",
        **metadata,
    ) -> str:
        """存入记忆"""
        metadata.setdefault("tier", tier)
        metadata.setdefault("created_at", datetime.now().isoformat())
        idx = self._store.add_fact(content, metadata)
        fact_id = f"mem_{idx:06d}"
        self.logger.debug(f"记忆已存储 [{tier}]: {fact_id}")
        # 自动持久化到磁盘
        self._auto_save()
        return fact_id

    def _auto_save(self) -> None:
        """自动持久化记忆到磁盘"""
        try:
            persistent_path = Path(self.config.memory_path).expanduser()
            self._store.save(persistent_path)
        except Exception as e:
            self.logger.warning(f"记忆自动保存失败: {e}")

    # ── Reflect ────────────────────────────────────────────────

    def reflect(self, session_summary: str) -> list[str]:
        """反思整合 — 提炼会话经验为持久规则

        流程：
        1. 提取关键洞察 → Core 记忆
        2. 标记成功/失败模式 → 建立关系边
        3. 过期规则 → Archival
        """
        ids: list[str] = []

        # 提取策略名称和关键指标
        name_match = re.search(r"策略[名称]*[：:]\s*(.+?)(?:\n|$)", session_summary)
        strategy_name = name_match.group(1).strip() if name_match else "未命名策略"

        sharpe_match = re.search(
            r"[Ss]harpe[_\s]*[Rr]atio[：:]\s*([\d.]+)", session_summary
        )
        sharpe = float(sharpe_match.group(1)) if sharpe_match else 0.0

        # 存储为核心记忆
        fact_id = self.remember(
            content=session_summary,
            tier="core",
            term=strategy_name,
            category="策略经验",
            experience_type="strategy",
            sharpe_ratio=sharpe,
            reflected_at=datetime.now().isoformat(),
        )
        ids.append(fact_id)

        # 建立与相关概念的关系
        for keyword, relation in [
            ("动量", "momentum"),
            ("价值", "value"),
            ("反转", "reversal"),
            ("波动率", "volatility"),
            ("成长", "growth"),
            ("质量", "quality"),
        ]:
            if keyword in session_summary:
                self.relate(fact_id, relation, "implements")
                ids.append(f"{fact_id}→{relation}")

        self.logger.info(f"反思完成: {fact_id} (Sharpe={sharpe:.3f})")
        return ids

    # ── Relate ─────────────────────────────────────────────────

    def relate(
        self,
        source: str,
        target: str,
        relation: str = "related_to",
        weight: float = 1.0,
    ) -> None:
        """建立知识实体关系"""
        self._store.add_relation(source, target, weight)
        self.logger.debug(f"关系: {source} --[{relation}]--> {target}")

    # ── Convenience: backward-compatible aliases ────────────────

    def search(
        self,
        query: str,
        k: int = 3,
        **filters,
    ) -> list[str]:
        """便捷方法: 检索并返回格式化字符串（兼容旧 KnowledgeService 调用）"""
        categories = filters.get("categories") or filters.get("category", [])
        terms = filters.get("terms") or filters.get("term", [])
        source_files = filters.get("source_files") or filters.get("source_file", [])
        results = self.recall(
            query,
            k=k,
            categories=categories if isinstance(categories, list) else [categories],
            terms=terms if isinstance(terms, list) else [terms],
            source_files=source_files if isinstance(source_files, list) else [source_files],
        )
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

    def save(self, content: str, metadata: dict[str, Any]) -> bool:
        """便捷方法: 保存知识"""
        try:
            self.remember(content, **metadata)
            return True
        except Exception as e:
            self.logger.error(f"保存失败: {e}")
            return False

    def save_experience(  # noqa: PLR0913
        self,
        strategy_code: str,
        strategy_name: str,
        design_rationale: str,
        backtest_result: dict,
        reflection: str,
        error_history: list[dict] | None = None,
    ) -> bool:
        """便捷方法: 保存策略经验"""
        try:
            content = f"""# 策略经验：{strategy_name}

## 设计思路
{design_rationale}

## 策略代码
```python
{strategy_code}
```

## 回测结果
```json
{json.dumps(backtest_result, ensure_ascii=False, indent=2)}
```

## 反思结论
{reflection}
"""
            if error_history:
                content += f"""
## 错误历史
{json.dumps(error_history, ensure_ascii=False, indent=2)}
"""

            # 兼容扁平结构（BacktestServiceImpl.run 实际返回）：
            # 优先取扁平字段，回退到嵌套 metrics 子字典；
            # 这样存入 experience 的 metadata 始终带真实数值，便于后续记忆检索匹配。
            metrics_payload = backtest_result.get("metrics", {}) or {}
            flat_keys = (
                "total_return",
                "annual_return",
                "sharpe_ratio",
                "max_drawdown",
                "volatility",
                "win_rate",
                "calmar_ratio",
                "sortino_ratio",
                "trading_days",
            )
            for key in flat_keys:
                if backtest_result.get(key) is not None:
                    metrics_payload.setdefault(key, backtest_result[key])

            self.remember(
                content,
                tier="core",
                term=strategy_name,
                category="策略经验",
                experience_type="strategy",
                backtest_metrics=metrics_payload,
                backtest_success=bool(backtest_result.get("success", True))
                and not backtest_result.get("error"),
            )
            return True
        except Exception as e:
            self.logger.error(f"保存经验失败: {e}")
            return False

    def search_experience(
        self,
        query: str,
        k: int = 3,
        min_sharpe: float | None = None,
    ) -> list[dict]:
        """便捷方法: 搜索历史经验

        min_sharpe 过滤：用显式 None 检查避免 `0 or fallback` 链
        把合法低值 sharpe=0 当成"缺失"误回退。
        """
        try:
            results = self.recall(query, k=k * 2, min_similarity=0.05)
            experiences: list[dict] = []
            for r in results:
                meta = r["metadata"]
                if meta.get("experience_type") != "strategy":
                    continue
                if min_sharpe is not None:
                    s = meta.get("sharpe_ratio")
                    if s is None:
                        s = (meta.get("backtest_metrics", {}) or {}).get(
                            "sharpe_ratio"
                        )
                    if s is None or s < min_sharpe:
                        continue

                content = r["content"]
                code_match = re.search(r"```python\n(.*?)```", content, re.DOTALL)
                code = code_match.group(1).strip() if code_match else ""
                rationale_match = re.search(
                    r"## 设计思路\n(.*?)## 策略代码", content, re.DOTALL
                )
                rationale = rationale_match.group(1).strip() if rationale_match else ""

                experiences.append(
                    {
                        "name": meta.get("term", ""),
                        "code": code,
                        "rationale": rationale,
                        "metrics": meta.get("backtest_metrics", {}),
                    }
                )
                if len(experiences) >= k:
                    break
            return experiences
        except Exception as e:
            self.logger.error(f"搜索经验失败: {e}")
            return []
