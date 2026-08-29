"""CI grep 卡口 — LLM 调用点分层检查（ADR-021）。

铁律：LLM 推理只允许出现在 agent 节点层（LangGraph 图节点 / ReAct 工具闭包 /
persona 节点）；services / tools / 数据基础设施等脚手架层不得内嵌 LLM 调用。

本脚本扫描 src/long_earn 下的 LLM 调用标记，白名单（逐条注明架构理由）之外
出现即失败。白名单扩容必须同步修改本脚本并注明理由——这是有意的摩擦。

用法：
    uv run python scripts/check_llm_call_sites.py

退出码：
    0 — 全部通过
    1 — 白名单外发现 LLM 调用点
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from long_earn.core.stdio import ensure_utf8_stdio

_ROOT = Path(__file__).resolve().parent.parent
_SCAN_DIR = _ROOT / "src" / "long_earn"

# LLM 调用标记（命中任一即视为 LLM 调用点）
_MARKERS: list[tuple[str, re.Pattern[str]]] = [
    ("llm_service.invoke", re.compile(r"llm_service\s*\.\s*invoke\s*\(")),
    ("require_llm()", re.compile(r"require_llm\s*\(\s*\)")),
    ("create_llm()", re.compile(r"\bcreate_llm\s*\(")),
    (".get_llm()", re.compile(r"\.\s*get_llm\s*\(")),
    ("ChatOpenAI()", re.compile(r"\bChatOpenAI\s*\(")),
    ("ChatOllama()", re.compile(r"\bChatOllama\s*\(")),
    ("ChatTongyi()", re.compile(r"\bChatTongyi\s*\(")),
    ("chat.completions", re.compile(r"\bchat\s*\.\s*completions\b")),
]

# 白名单：相对路径前缀 → 架构理由（ADR-021 §C）。
# 仅两类路径允许出现 LLM 调用：
#   1. agent 节点层（推理归属地）
#   2. LLM 基础设施（工厂 / 服务封装 / 契约文档）
_ALLOWED: dict[str, str] = {
    # ── LLM 基础设施 ──
    "src/long_earn/config.py": "DI 容器（require_llm 非空保证定义）",
    "src/long_earn/utils/llm_factory.py": "LLM 实例工厂",
    "src/long_earn/services/__init__.py": "服务 Protocol 契约文档",
    "src/long_earn/services/llm_service.py": "LLM 服务封装（基础设施）",
    "src/long_earn/services/kimi_web_search.py": "联网搜索 Provider（基础设施能力，非推理）",
    # ── agent 节点层 ──
    "src/long_earn/master_agent.py": "MasterAgent ReAct 主控",
    "src/long_earn/event_inference/agents/": "事件推理 agent 节点",
    "src/long_earn/operator_dev/agents.py": "算子研发 agent 节点",
    "src/long_earn/stock_analysis/subgraph.py": "子图节点（resolve_stock_ref LLM 兜底）",
    "src/long_earn/stock_analysis/agents/": "五视角分析师 agent 节点",
    "src/long_earn/skills/personas/": "大师 persona LLM 节点（HTR 遗留，清退中）",
    "src/long_earn/strategy_rd/research_agent.py": "ToG ResearchAgent（agent 层）",
    "src/long_earn/strategy_rd/escape_hatch.py": "失败分类（规则先行、LLM 兜底）",
    # ── HTR 遗留线（清退中，见 TODO；冻结新增，迁移后整体移除）──
    "src/long_earn/strategy_rd/htr_subgraph.py": "HTR 遗留线",
    "src/long_earn/strategy_rd/agents/": "HTR 遗留线",
}


def _is_allowed(rel_path: str) -> str | None:
    """返回命中的白名单理由；不在白名单返回 None。"""
    for prefix, reason in _ALLOWED.items():
        if rel_path.startswith(prefix):
            return reason
    return None


def main() -> int:
    ensure_utf8_stdio()

    violations: list[str] = []
    scanned = 0
    for py_file in sorted(_SCAN_DIR.rglob("*.py")):
        scanned += 1
        rel_path = py_file.relative_to(_ROOT).as_posix()
        reason = _is_allowed(rel_path)
        if reason is not None:
            continue

        text = py_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for name, pattern in _MARKERS:
                if pattern.search(line):
                    violations.append(
                        f"{rel_path}:{lineno}: 发现 LLM 调用标记 [{name}]: {line.strip()}"
                    )
                    break

    if violations:
        print(f"❌ 白名单外发现 {len(violations)} 处 LLM 调用点（ADR-021）：\n")
        for item in violations:
            print(f"  {item}")
        print(
            "\nLLM 推理只允许存在于 agent 节点层（ADR-021）。"
            "\n如确属 agent 层新文件，请在 scripts/check_llm_call_sites.py "
            "白名单中登记并注明架构理由。"
        )
        return 1

    print(
        f"✅ LLM 调用点分层检查通过（扫描 {scanned} 个文件，白名单 {len(_ALLOWED)} 项）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
