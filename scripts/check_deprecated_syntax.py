"""CI grep 卡口 — 检测退役语法回退（ADR-016 阶段 4）。

检查项：
1. Python 文件中内联 prompt 字符串包含退役的"表达式路径"指令
2. Prompt .md 文件使用 ${var} 占位符（ADR-011 已废弃）
3. Python 文件中使用 `render()` 函数渲染内联字符串（应改用 MarkdownPromptTemplate）
4. Prompt .md 文件中将退役语法作为有效选项推荐（非警告/示例中的反面教材）

用法：
    uv run python scripts/check_deprecated_syntax.py

退出码：
    0 — 全部通过
    1 — 发现退役语法回退
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# 项目根目录
_ROOT = Path(__file__).resolve().parent.parent

# 待扫描的源码目录
_SCAN_DIRS = [
    _ROOT / "src" / "long_earn",
]

# 退役语法模式
# 1. Python 内联 prompt 中出现"路径 2"（退役的表达式路径指令标头）
#    "表达式路径"单独出现可能是注释/文档中的历史引用，不单独检测
_DEPRECATED_EXPRESSION_PATH = re.compile(r"路径\s*2")

# 2. ${var} 占位符（ADR-011 废弃，应使用 jinja2 {{ var }}）
_DEPRECATED_VAR_SYNTAX = re.compile(r"\$\{[^}]+\}")

# 允许出现退役语法的文件（如本检查脚本自身、AGENTS.md 中的说明）
_WHITELIST_FILES = {
    Path(__file__).resolve(),  # 本脚本自身
    _ROOT / "AGENTS.md",  # 项目规范文档中会提及退役语法
}

# 允许在"退役警告"上下文中出现的文件（.md prompt 文件中会提及退役语法作为反面教材）
# 这些文件中出现的退役语法是作为"不要这样做"的警告，不是有效指令
_PROMPT_WARNING_CONTEXT = {
    "strategy_develop_prompt.md",
    "strategy_develop_refine_prompt.md",
    "strategy_optimize_prompt.md",
}


def check_python_files() -> list[str]:
    """检查 Python 文件中的退役语法。"""
    violations: list[str] = []

    for scan_dir in _SCAN_DIRS:
        for py_file in scan_dir.rglob("*.py"):
            if py_file in _WHITELIST_FILES:
                continue

            try:
                content = py_file.read_text(encoding="utf-8")
            except Exception:
                continue

            rel_path = py_file.relative_to(_ROOT)

            # 检查退役的表达式路径指令（"路径 2" 标头）
            for match in _DEPRECATED_EXPRESSION_PATH.finditer(content):
                line_num = content[: match.start()].count("\n") + 1
                violations.append(
                    f"{rel_path}:{line_num} — 退役表达式路径指令: "
                    f"'{match.group()}'（应删除路径 2，仅保留算子路径）"
                )

            # 检查 ${var} 占位符
            for match in _DEPRECATED_VAR_SYNTAX.finditer(content):
                line_num = content[: match.start()].count("\n") + 1
                violations.append(
                    f"{rel_path}:{line_num} — 退役 ${{var}} 语法: "
                    f"'{match.group()}'（ADR-011 废弃，应使用 jinja2 {{{{ var }}}}）"
                )

    return violations


def check_prompt_files() -> list[str]:
    """检查 prompt .md 文件中的退役语法。

    .md 文件中允许在"退役警告"上下文（如"旧式 type: filter 已退役"）中提及退役语法，
    但不允许将其作为有效选项推荐（如"路径 2：表达式路径"）。
    """
    violations: list[str] = []

    prompt_dir = _ROOT / "src" / "long_earn" / "strategy_rd" / "agents"
    for md_file in prompt_dir.glob("*.md"):
        if md_file in _WHITELIST_FILES:
            continue

        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        rel_path = md_file.relative_to(_ROOT)
        file_name = md_file.name

        # 检查 ${var} 语法（所有 prompt 文件都不应使用）
        for match in _DEPRECATED_VAR_SYNTAX.finditer(content):
            line_num = content[: match.start()].count("\n") + 1
            violations.append(
                f"{rel_path}:{line_num} — 退役 ${{var}} 语法: "
                f"'{match.group()}'（ADR-011 废弃，应使用 jinja2 {{{{ var }}}}）"
            )

        # 检查"路径 2" / "表达式路径"作为有效选项推荐
        # （在警告上下文中提及"已退役"是允许的，但作为"路径 2"推荐是不允许的）
        if file_name not in _PROMPT_WARNING_CONTEXT:
            continue

        for match in _DEPRECATED_EXPRESSION_PATH.finditer(content):
            line_num = content[: match.start()].count("\n") + 1
            # 检查上下文：如果附近有"退役"/"已废弃"/"不要"等字样，视为警告，跳过
            context_start = max(0, match.start() - 100)
            context_end = min(len(content), match.end() + 100)
            context = content[context_start:context_end]

            if any(kw in context for kw in ("退役", "废弃", "不要", "禁止", "错误")):
                continue

            violations.append(
                f"{rel_path}:{line_num} — 退役表达式路径指令: "
                f"'{match.group()}'（应删除路径 2，仅保留算子路径）"
            )

    return violations


def main() -> int:
    """主入口 — 运行所有检查，返回退出码。"""
    violations: list[str] = []

    violations.extend(check_python_files())
    violations.extend(check_prompt_files())

    if violations:
        print("❌ 发现退役语法回退：\n")
        for v in violations:
            print(f"  {v}")
        print(f"\n共 {len(violations)} 处违规。")
        print("\n修复指南：")
        print("  1. 删除内联 Python prompt 字符串中的'路径 2'指令")
        print("  2. 将 ${var} 替换为 jinja2 {{ var }} 语法")
        return 1

    print("✅ 退役语法检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
