"""Markdown Chat Prompt Template — ADR-011 Phase 4

基于 ``ChatPromptTemplate.from_messages``，从 .md 文件 frontmatter 的
``messages`` 字段解析多消息聊天模板。

frontmatter 格式示例::

    ---
    version: 2.0.0
    description: 描述
    messages:
      system: |
        你是{{ role }}分析师
        ## 输出格式
        {{ output_format }}
      human: |
        股票数据：{{ stock_data }}
        请分析。
      placeholder: examples
    ---

注意：frontmatter 里的消息模板用 jinja2 语法 ``{{ var }}``（与
MarkdownPromptTemplate 一致）。但 ``ChatPromptTemplate.from_messages`` 默认用
f-string ``{var}``——构造时把每个消息体包装成
``PromptTemplate(template=..., template_format='jinja2')``，再用对应的
``*MessagePromptTemplate(prompt=...)`` 包一层传入 ``from_messages``，以覆盖默认
f-string 语义。

无 ``messages`` frontmatter 字段时，退化为单消息 human 模式（用整个 body 作为
human 消息），向后兼容 ``MarkdownPromptTemplate`` 的单 prompt 用法。

版本：1.0.0
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import BaseMessage
from langchain_core.prompts import (
    AIMessagePromptTemplate,
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    MessagesPlaceholder,
    PromptTemplate,
    SystemMessagePromptTemplate,
)

__version__ = "1.0.0"

# jinja2 变量提取正则：匹配 {{ var }} 形式（双花括号 + 空格 + 标识符）
_VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_]\w*)\s*\}\}")

# frontmatter 角色 key → MessagePromptTemplate 构造器
_ROLE_BUILDERS: dict[str, type] = {
    "system": SystemMessagePromptTemplate,
    "human": HumanMessagePromptTemplate,
    "ai": AIMessagePromptTemplate,
}


def _extract_variables(template: str) -> list[str]:
    """提取 jinja2 模板变量名（去重保序）。"""
    seen: set[str] = set()
    result: list[str] = []
    for m in _VAR_PATTERN.finditer(template):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """解析 YAML frontmatter，返回 (metadata, body)。

    与 MarkdownPromptTemplate 的简单行解析不同，此处用 yaml.safe_load 以支持
    ``messages`` 嵌套 dict / block scalar 语法。

    无 frontmatter 时返回 ``({}, content.strip())``。
    """
    frontmatter_pattern = r"^---\s*\n(.*?)\n---\s*\n?(.*)$"
    match = re.search(frontmatter_pattern, content, re.DOTALL)
    if not match:
        return {}, content.strip()
    frontmatter_text = match.group(1)
    body = match.group(2)
    metadata = yaml.safe_load(frontmatter_text)
    if not isinstance(metadata, dict):
        return {}, content.strip()
    return metadata, body.strip()


def _make_prompt_template(body: str) -> PromptTemplate:
    """构造 jinja2 PromptTemplate（覆盖 ChatPromptTemplate 默认 f-string）。"""
    return PromptTemplate(
        template=body,
        template_format="jinja2",
        input_variables=_extract_variables(body),
    )


class MarkdownChatPromptTemplate:
    """Markdown 多消息聊天提示词模板

    从 .md 文件 frontmatter 的 ``messages`` 字段解析消息划分，构造
    ``ChatPromptTemplate``。每个消息体用 jinja2 语法 ``{{ var }}``。

    支持的 frontmatter ``messages`` key：
    - ``system`` / ``human`` / ``ai``：消息角色，值为 jinja2 模板字符串
    - ``placeholder``：``MessagesPlaceholder`` 变量名，可为字符串或字符串列表

    无 ``messages`` 字段时退化为单 human 消息（用整个 body）。
    """

    def __init__(
        self,
        template_file: str,
        caller_file: str | None = None,
    ):
        """初始化

        Args:
            template_file: 提示词文件路径（相对或绝对）
            caller_file: 调用者文件路径（使用 __file__），相对路径时需要

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: frontmatter ``messages`` 格式不正确
        """
        template_path = Path(template_file)
        if not template_path.is_absolute() and caller_file:
            template_path = Path(caller_file).parent / template_file

        template_content = template_path.read_text(encoding="utf-8")
        metadata, body = _parse_frontmatter(template_content)

        self.template_file = template_path
        self.name = template_path.stem
        self.version = metadata.get("version", "1.0.0")
        self.description = metadata.get("description", "")
        self.template_body = body

        messages_field = metadata.get("messages")
        if isinstance(messages_field, dict) and messages_field:
            self._chat_prompt = self._build_from_messages(messages_field)
            self._is_chat_mode = True
        else:
            # 退化为单 human 消息（body 整体作为 human 模板）
            self._chat_prompt = self._build_single_human(body)
            self._is_chat_mode = False

    def _build_from_messages(
        self, messages_field: dict[str, Any]
    ) -> ChatPromptTemplate:
        """从 frontmatter messages dict 构造 ChatPromptTemplate。

        按 frontmatter 中 key 的出现顺序组装消息（yaml.safe_load 返回的 dict
        保持插入顺序），使 ``placeholder`` 能落在 frontmatter 指定的位置。
        """
        message_templates: list[Any] = []

        for key, value in messages_field.items():
            if key in _ROLE_BUILDERS:
                if not isinstance(value, str):
                    raise ValueError(
                        f"messages.{key} 必须是字符串模板，"
                        f"实际类型 {type(value).__name__}"
                    )
                builder = _ROLE_BUILDERS[key]
                message_templates.append(builder(prompt=_make_prompt_template(value)))
            elif key == "placeholder":
                for var_name in self._normalize_placeholder(value):
                    message_templates.append(
                        MessagesPlaceholder(variable_name=var_name)
                    )
            # 其他未知 key 静默忽略，保持向前兼容

        if not message_templates:
            raise ValueError("messages 字段为空或无有效消息角色")

        return ChatPromptTemplate.from_messages(message_templates)

    @staticmethod
    def _normalize_placeholder(placeholder: Any) -> list[str]:
        """将 placeholder 字段规范化为变量名列表。"""
        if isinstance(placeholder, str):
            return [placeholder]
        if isinstance(placeholder, list):
            names: list[str] = []
            for item in placeholder:
                if not isinstance(item, str):
                    raise ValueError(
                        f"placeholder 列表元素必须是字符串，"
                        f"实际类型 {type(item).__name__}"
                    )
                names.append(item)
            return names
        raise ValueError(
            f"placeholder 必须是字符串或字符串列表，"
            f"实际类型 {type(placeholder).__name__}"
        )

    def _build_single_human(self, body: str) -> ChatPromptTemplate:
        """退化为单 human 消息。"""
        return ChatPromptTemplate.from_messages(
            [HumanMessagePromptTemplate(prompt=_make_prompt_template(body))]
        )

    def format_messages(self, **kwargs: Any) -> list[BaseMessage]:
        """渲染并返回 BaseMessage 列表。"""
        return self._chat_prompt.format_messages(**kwargs)

    def format(self, **kwargs: Any) -> str:
        """渲染并返回拼接字符串（向后兼容单 prompt 用法）。

        将所有消息内容按顺序拼接，消息之间用换行分隔。退化为单 human 消息时
        等价于 ``MarkdownPromptTemplate.format``。
        """
        messages = self.format_messages(**kwargs)
        return "\n".join(
            m.content if isinstance(m.content, str) else str(m.content)
            for m in messages
        )

    def __repr__(self) -> str:
        return (
            f"MarkdownChatPromptTemplate(name='{self.name}', "
            f"file='{self.template_file}', "
            f"chat_mode={self._is_chat_mode})"
        )
