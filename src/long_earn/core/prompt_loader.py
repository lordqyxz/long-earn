"""提示词加载服务模块

提供统一的提示词加载和管理功能，支持 Markdown 格式的提示词模板。

使用 ${var} 占位符语法（POSIX/bash/JS 模板字面量同款），跨语言可移植。
渲染由 core.render.render() 纯函数完成，不依赖 LangChain。

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

版本：2.0.0
"""

from __future__ import annotations

__version__ = "2.0.0"

import re
from pathlib import Path
from typing import Any

from long_earn.core.render import extract_variables, render


class MarkdownPromptTemplate:
    """Markdown 格式的提示词模板

    从 Markdown 文件加载提示词，支持：
    - 自动推断文件路径（基于调用者文件位置）
    - 变量占位符（使用 ${variable_name} 格式）
    - 缓存机制，避免重复读取文件
    - 支持 Markdown frontmatter 元数据（版本、描述等）
    - 渲染由 core.render.render() 纯函数完成，不依赖 LangChain
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
            template_path = Path(caller_file).parent / template_path

        template_content = template_path.read_text(encoding="utf-8")

        metadata, template_body = self._parse_frontmatter(template_content)

        if input_variables is None:
            input_variables = extract_variables(template_content)

        self.template = template_body
        self.input_variables = input_variables or []
        self._partial_variables = partial_variables or {}
        self.name = template_path.stem
        self.template_file = template_path
        self.version = metadata.get("version", "1.0.0")
        self.description = metadata.get("description", "")

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
        """渲染模板，将 ${var} 替换为传入的值。

        合并 partial_variables 和 kwargs，kwargs 优先。
        """
        variables = {**self._partial_variables, **kwargs}
        return render(self.template, variables)

    def __repr__(self) -> str:
        return (
            f"MarkdownPromptTemplate(name='{self.name}', "
            f"file='{self.template_file}', "
            f"variables={self.input_variables})"
        )
