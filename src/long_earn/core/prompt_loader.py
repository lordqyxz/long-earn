"""提示词加载服务模块

提供统一的提示词加载和管理功能，支持 Markdown 格式的提示词模板。

使用 jinja2 占位符语法（`{{ var }}`，双花括号 + 空格），默认不 HTML 转义。
渲染委托 langchain_core.prompts.PromptTemplate(template_format='jinja2')，
底层为 SandboxedEnvironment（阻断 __class__ 等逃逸）。

使用示例：
    from long_earn.core.prompt_loader import MarkdownPromptTemplate

    # 方式 1：最简方式（推荐）- 直接指定相对路径
    class MyAgent:
        def __init__(self, context: "RuntimeContext"):
            self.prompt = MarkdownPromptTemplate.from_file(
                "prompts/my_agent_prompt.md",
                caller_file=__file__,
            )

        def run(self, query: str, context: str):
            formatted = self.prompt.format(query=query, context=context)
            return self.llm.invoke(formatted)

    # 方式 2：使用绝对路径
    prompt = MarkdownPromptTemplate.from_file(
        "/absolute/path/to/prompt.md",
    )

    # 方式 3：保持向后兼容 - 使用 name 和 caller_file
    prompt = MarkdownPromptTemplate(
        name="my_prompt",
        caller_file=__file__,
    )

版本：3.0.0
"""

from __future__ import annotations

__version__ = "3.0.0"

import re
from pathlib import Path
from typing import Any

from langchain_core.prompts import PromptTemplate

# jinja2 变量提取正则：匹配 {{ var }} 形式（双花括号 + 空格 + 标识符）
_VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_]\w*)\s*\}\}")


def render(template: str, variables: dict[str, Any]) -> str:
    """渲染模板，将 `{{ var }}` 替换为传入的值。

    基于 langchain_core.prompts.PromptTemplate(template_format='jinja2')，
    默认不 HTML 转义（LLM 提示词场景无需转义）。

    规则：
    - `{{ var }}` → str(variables[var])
    - 缺失变量输出空串（jinja2 Undefined 语义）
    - 不转义 `<>&"` 等字符
    - SandboxedEnvironment 阻断 `__class__`/`__globals__` 等逃逸

    Args:
        template: 模板字符串
        variables: 变量字典

    Returns:
        渲染后的字符串
    """
    return PromptTemplate(
        template=template,
        template_format="jinja2",
        input_variables=_extract_variables(template),
    ).format(**variables)


def _extract_variables(template: str) -> list[str]:
    """提取模板中所有 `{{ var }}` 变量名（去重保序）。"""
    seen: set[str] = set()
    result: list[str] = []
    for m in _VAR_PATTERN.finditer(template):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


class MarkdownPromptTemplate:
    """Markdown 格式的提示词模板

    从 Markdown 文件加载提示词，支持：
    - 自动推断文件路径（基于调用者文件位置）
    - 变量占位符（使用 `{{ variable_name }}` 格式，jinja2 语法）
    - 缓存机制，避免重复读取文件
    - 支持 Markdown frontmatter 元数据（版本、描述等）
    - 渲染委托 langchain_core.prompts.PromptTemplate(template_format='jinja2')，
      默认不 HTML 转义，底层 SandboxedEnvironment 防止 prompt 注入逃逸
    """

    def __init__(
        self,
        template_file: str,
        input_variables: list[str] | None = None,
        caller_file: str | None = None,
        partial_variables: dict[str, Any] | None = None,
        validate_template: bool = True,  # noqa: ARG002  向后兼容参数
    ):
        """初始化 Markdown 提示词模板

        Args:
            template_file: 提示词文件路径（相对路径或绝对路径）
            input_variables: 输入变量列表，如果为 None 则自动从模板中提取
            caller_file: 调用者文件路径（使用 __file__），当使用相对路径时需要
            partial_variables: 部分变量字典，用于预填充某些变量
            validate_template: 是否验证模板（向后兼容，内部已不再需要）

        Raises:
            FileNotFoundError: 当提示词文件不存在时
            ValueError: 当模板格式不正确时
        """
        template_path = Path(template_file)
        if not template_path.is_absolute() and caller_file:
            template_path = Path(caller_file).parent / template_file

        template_content = template_path.read_text(encoding="utf-8")

        metadata, template_body = self._parse_frontmatter(template_content)

        if input_variables is None:
            input_variables = _extract_variables(template_body)

        self.template = template_body
        self.input_variables = input_variables or []
        self._partial_variables = partial_variables or {}
        self.name = template_path.stem
        self.template_file = template_path
        self.version = metadata.get("version", "1.0.0")
        self.description = metadata.get("description", "")

        # 构造 jinja2 PromptTemplate 委托渲染
        # 注：partial_variables 必须传 dict（{}）而非 None，否则
        # langchain_core 1.3 pre_init_validation 会在 None 上做 `in` 操作报错
        self._prompt_template = PromptTemplate(
            template=template_body,
            template_format="jinja2",
            input_variables=self.input_variables,
            partial_variables=self._partial_variables,
        )

    @staticmethod
    def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
        """解析 Markdown frontmatter 元数据

        支持 YAML 格式的 frontmatter：
        ```
        ---
        version: 1.0.0
        description: 提示词描述
        author: 作者
        ---

        # 提示词正文
        ```
        """
        frontmatter_pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
        match = re.search(frontmatter_pattern, content, re.DOTALL)

        if match:
            frontmatter_text = match.group(1)
            body = match.group(2)
            metadata: dict[str, Any] = {}

            for line in frontmatter_text.strip().split("\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    metadata[key.strip()] = value.strip()

            return metadata, body.strip()
        return {}, content.strip()

    def format(self, **kwargs: Any) -> str:
        """渲染模板，将 `{{ var }}` 替换为传入的值。

        合并 partial_variables 和 kwargs，kwargs 优先。
        """
        return self._prompt_template.format(**kwargs)

    def __repr__(self) -> str:
        return (
            f"MarkdownPromptTemplate(name='{self.name}', "
            f"file='{self.template_file}', "
            f"variables={self.input_variables})"
        )
