"""记忆服务实现 — 基于 MemoryStore 的 3-Tier 记忆系统

Working / Core / Archival 三级记忆，遵循 Letta/MemGPT 模式。
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from long_earn.config import RuntimeContext
from long_earn.memory.store import MemoryStore
from long_earn.services import MemoryService


class MemoryServiceImpl(MemoryService):
    """3-Tier 记忆服务"""

    def __init__(self, context: "RuntimeContext"):
        self.context = context
        self.config = context.config
        self.logger = context.logger
        self._store = MemoryStore()
        self._initialized = False

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
        """语义检索记忆"""
        try:
            return self._store.search(query, k=k, **filters)
        except Exception as e:
            self.logger.error(f"记忆检索失败: {e}")
            return []

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
        return fact_id

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
        categories: list[str] | None = None,
        terms: list[str] | None = None,
        source_files: list[str] | None = None,
    ) -> list[str]:
        """便捷方法: 检索并返回格式化字符串（兼容旧 KnowledgeService 调用）"""
        results = self.recall(
            query,
            k=k,
            categories=categories or [],
            terms=terms or [],
            source_files=source_files or [],
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

            self.remember(
                content,
                tier="core",
                term=strategy_name,
                category="策略经验",
                experience_type="strategy",
                backtest_metrics=backtest_result.get("metrics", {}),
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
        """便捷方法: 搜索历史经验"""
        try:
            results = self.recall(query, k=k * 2, min_similarity=0.05)
            experiences: list[dict] = []
            for r in results:
                meta = r["metadata"]
                if meta.get("experience_type") != "strategy":
                    continue
                if min_sharpe:
                    s = meta.get("sharpe_ratio", 0) or meta.get(
                        "backtest_metrics", {}
                    ).get("sharpe_ratio", 0)
                    if s < min_sharpe:
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
