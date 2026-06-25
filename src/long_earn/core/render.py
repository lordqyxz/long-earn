"""纯函数模板渲染器

使用 ${var} 占位符语法，跨语言可移植（POSIX/bash/JS 模板字面量同款）。
零第三方依赖，基于 string.Template.safe_substitute。
"""

from __future__ import annotations

import re
from string import Template
from typing import Any

_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def render(template: str, variables: dict[str, Any]) -> str:
    """渲染模板，将 ${name} 替换为 str(variables[name])。

    规则：
    - ${name} → str(variables[name])
    - 缺失原样保留（safe_substitute 语义）
    - $$ → 字面 $（跨语言一致转义）
    """
    return Template(template).safe_substitute(**variables)


def extract_variables(template: str) -> list[str]:
    """提取模板中所有 ${var} 变量名（去重保序）。"""
    seen: set[str] = set()
    result: list[str] = []
    for m in _VAR_PATTERN.finditer(template):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result
