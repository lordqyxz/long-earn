"""知识存储工具

基于 numpy/pandas 记忆系统，提供知识检索和持久化功能。
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from long_earn.memory.store import MemoryStore
from long_earn.services.logger_service import LoggerServiceImpl

LOGGER = LoggerServiceImpl()


class _StoreHolder:
    """模块级单例容器"""

    store: MemoryStore | None = None


def _get_store() -> MemoryStore:
    if _StoreHolder.store is None:
        _StoreHolder.store = MemoryStore()
    return _StoreHolder.store


def get_vector_store() -> MemoryStore:
    """获取记忆存储实例（兼容旧接口名称）"""
    return _get_store()


def search_knowledge(
    query: str,
    k: int = 3,
    categories: list[str] | None = None,
    terms: list[str] | None = None,
    source_files: list[str] | None = None,
) -> list[str]:
    """搜索知识库"""
    try:
        store = _get_store()
        return store.search_as_strings(
            query,
            k=k,
            categories=categories,
            terms=terms,
            source_files=source_files,
        )
    except Exception as e:
        LOGGER.error(f"搜索知识库失败: {e}")
        return []


def init_system():
    """系统初始化函数 — 扫描 init 目录并加载到记忆系统"""
    LOGGER.info("开始系统初始化...")
    store = _get_store()

    init_dir = Path(os.getenv("INIT_DIR", "./init"))
    if init_dir.exists():
        count = store.load_directory(init_dir)
        if count > 0:
            LOGGER.info(f"知识库加载完成，共 {count} 条事实")

            # 持久化
            memory_path = os.path.expanduser(
                os.getenv("MEMORY_PATH", "~/.long_earn/memory.npz")
            )
            Path(memory_path).parent.mkdir(parents=True, exist_ok=True)
            store.save(memory_path)

    LOGGER.info("系统初始化完成")


def save_experience(  # noqa: PLR0913
    strategy_code: str,
    strategy_name: str,
    design_rationale: str,
    backtest_result: dict,
    reflection: str,
    error_history: list[dict] | None = None,
) -> bool:
    """保存策略开发经验到知识库"""
    try:
        store = _get_store()

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

        content += f"""
---
**创建时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
"""

        store.add_fact(
            content,
            {
                "source_file": "experience.md",
                "term": strategy_name,
                "category": "策略经验",
                "section_level": 1,
                "experience_type": "strategy",
                "backtest_metrics": backtest_result.get("metrics", {}),
            },
        )

        LOGGER.info(f"策略经验已保存: {strategy_name}")
        return True
    except Exception as e:
        LOGGER.error(f"保存经验失败: {e}")
        return False


def search_experience(
    query: str,
    k: int = 3,
    min_sharpe: float | None = None,
) -> list[dict]:
    """搜索历史策略经验"""
    try:
        store = _get_store()
        results = store.search(query, k=k * 2, min_similarity=0.05)

        experiences = []
        for r in results:
            meta = r["metadata"]

            if meta.get("experience_type") != "strategy":
                continue

            if min_sharpe:
                metrics = meta.get("backtest_metrics", {})
                sharpe = metrics.get("sharpe_ratio", 0) or metrics.get("sharpe", 0)
                if sharpe < min_sharpe:
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
        LOGGER.error(f"搜索经验失败: {e}")
        return []
