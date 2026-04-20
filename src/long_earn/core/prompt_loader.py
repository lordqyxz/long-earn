"""提示词加载服务模块

提供统一的提示词加载和管理功能，支持 Markdown 格式的提示词模板。

主要功能：
- 自动推断提示词文件路径
- 支持 Markdown 格式的提示词模板
- 内置缓存机制
- 类型安全的提示词加载

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

版本：1.0.0
"""

__version__ = "1.0.0"

import re
from pathlib import Path
from typing import Any

from langchain_core.prompts import PromptTemplate


class MarkdownPromptTemplate(PromptTemplate):
    """Markdown 格式的提示词模板

    从 Markdown 文件加载提示词，支持：
    - 自动推断文件路径（基于调用者文件位置）
    - 变量占位符（使用 {variable_name} 格式）
    - 缓存机制，避免重复读取文件
    - 类型检查和验证
    - 支持 Markdown frontmatter 元数据（版本、描述等）
    """

    model_config = {"extra": "allow"}

    def __init__(
        self,
        template_file: str,
        input_variables: list[str] | None = None,
        caller_file: str | None = None,
        partial_variables: dict[str, Any] | None = None,
        validate_template: bool = True,
    ):
        """初始化 Markdown 提示词模板

        Args:
            template_file: 提示词文件路径（相对路径或绝对路径）
            input_variables: 输入变量列表，如果为 None 则自动从模板中提取
            caller_file: 调用者文件路径（使用 __file__），当使用相对路径时需要
            partial_variables: 部分变量字典，用于预填充某些变量
            validate_template: 是否验证模板

        Raises:
            FileNotFoundError: 当提示词文件不存在时
            ValueError: 当模板格式不正确时

        示例:
            # 最简方式（推荐）
            prompt = MarkdownPromptTemplate("prompts/my_prompt.md")

            # 带输入变量
            prompt = MarkdownPromptTemplate(
                "prompts/my_prompt.md",
                ["query", "context"]
            )

            # 相对路径（需要 caller_file）
            prompt = MarkdownPromptTemplate(
                "prompts/my_prompt.md",
                caller_file=__file__
            )
        """
        template_path = Path(template_file)
        if not template_path.is_absolute() and caller_file:
            # 相对路径则相对于调用者文件
            template_path = Path(caller_file).parent / template_path

        # 读取模板内容
        template_content = template_path.read_text(encoding="utf-8")

        # 解析 frontmatter 元数据
        metadata, template_body = self._parse_frontmatter(template_content)

        # 将 {{variable}} 转换为 {variable} 格式以便 langchain 处理
        # 注意：只在非代码块区域进行转换
        template_body = self._convert_variables(template_body)

        # 自动提取输入变量（如果没有提供）
        if input_variables is None:
            input_variables = self._extract_variables(template_content)

        # 调用父类初始化
        super().__init__(
            template=template_body,
            input_variables=input_variables or [],
            partial_variables=partial_variables or {},
            validate_template=validate_template,
        )

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

        Args:
            content: Markdown 文件内容

        Returns:
            (metadata, body) 元组，包含元数据字典和正文字符串
        """
        import re

        # 匹配 frontmatter 块
        frontmatter_pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
        match = re.search(frontmatter_pattern, content, re.DOTALL)

        if match:
            # 解析 YAML frontmatter
            frontmatter_text = match.group(1)
            body = match.group(2)
            metadata = {}

            # 简单的 YAML 解析（支持键值对）
            for line in frontmatter_text.strip().split("\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    metadata[key.strip()] = value.strip()

            return metadata, body.strip()
        else:
            # 没有 frontmatter，返回空元数据
            return {}, content.strip()

    @classmethod
    def _infer_template_path(cls, name: str, caller_file: str) -> Path:
        """根据调用者文件自动推断提示词文件路径

        搜索策略：
        1. 调用者文件同级的 {name}.md
        2. 调用者文件同级 prompts 目录下的 {name}.md
        3. 调用者文件上级 prompts 目录下的 {name}.md

        Args:
            name: 提示词名称
            caller_file: 调用者文件路径

        Returns:
            提示词文件路径

        Raises:
            FileNotFoundError: 当所有可能的位置都不存在时
        """
        caller_path = Path(caller_file)
        caller_dir = caller_path.parent
        possible_paths = [
            caller_dir / f"{name}.md",
            caller_dir / "prompts" / f"{name}.md",
            caller_dir.parent / "prompts" / f"{name}.md",
        ]

        # 尝试找到第一个存在的文件
        for path in possible_paths:
            if path.exists():
                return path

        # 如果都没找到，返回第一个可能的位置（会在后续检查时报错）
        return possible_paths[0]

    @classmethod
    def _load_template(cls, template_path: Path) -> str:
        """加载模板内容

        Args:
            template_path: 模板文件路径

        Returns:
            模板内容字符串

        Raises:
            FileNotFoundError: 当文件不存在时
        """
        # 检查文件是否存在
        if not template_path.exists():
            raise FileNotFoundError(
                f"提示词文件不存在：{template_path}\n"
                f"请确保文件位于以下位置之一：\n"
                f"1. 调用者文件同级目录：{template_path.parent}/{template_path.name}\n"
                f"2. 调用者文件同级 prompts 目录：{template_path.parent}/prompts/{template_path.name}\n"
                f"3. 调用者文件上级 prompts 目录：{template_path.parent.parent}/prompts/{template_path.name}"
            )

        return template_path.read_text(encoding="utf-8")

    @staticmethod
    def _extract_variables(template_content: str) -> list[str]:
        """从模板内容中提取变量名

        只支持双大括号格式，且只在非代码块区域提取：
        - {{variable}} - 用于避免与代码中的字典冲突
        """
        variables = set()

        # 只在非代码块区域提取变量
        lines = template_content.split("\n")
        in_code_block = False

        for line in lines:
            # 检查是否在代码块内
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                continue

            if not in_code_block:
                # 提取 {{variable}} 格式的变量
                pattern_double = r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}"
                matches_double = re.findall(pattern_double, line)
                variables.update(matches_double)

        return list(variables)

    @staticmethod
    def _convert_variables(template_body: str) -> str:
        """将 {{variable}} 转换为 {variable} 格式
        智能转换：
        1. 跳过代码块内的内容（``` 包裹的部分）
        2. 跳过内联代码（` 包裹的部分）
        3. 只转换独立的变量标记
        4. 将代码块和内联代码中的 {} 转义为 {{}} 以避免被 langchain 误识别
        """
        lines = template_body.split("\n")
        result_lines = []
        in_code_block = False

        for line in lines:
            # 检查是否在代码块内
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                result_lines.append(line)
                continue

            if in_code_block:
                # 代码块内不转换变量，但需要转义大括号
                escaped_line = line.replace("{", "{{").replace("}", "}}")
                result_lines.append(escaped_line)
            else:
                # 非代码块，需要处理内联代码和变量转换
                # 先转义内联代码中的大括号，再转换变量标记
                converted_line = MarkdownPromptTemplate._convert_inline_line(line)
                result_lines.append(converted_line)

        return "\n".join(result_lines)

    @staticmethod
    def _convert_inline_line(line: str) -> str:
        """处理非代码块行的变量转换

        1. 先保护内联代码（反引号包裹的内容）中的大括号
        2. 再将 {{variable}} 转换为 {variable}
        3. 最后恢复内联代码中的大括号（转义形式）
        """
        # 分割行为内联代码段和非内联代码段
        parts = re.split(r"(`[^`]+`)", line)
        result_parts = []

        for i, part in enumerate(parts):
            if part.startswith("`") and part.endswith("`") and len(part) > 2:
                # 内联代码段：转义大括号以避免被 langchain 误识别
                escaped_part = part.replace("{", "{{").replace("}", "}}")
                result_parts.append(escaped_part)
            else:
                # 非内联代码段：将 {{variable}} 转换为 {variable}
                converted_part = re.sub(
                    r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}", r"{\1}", part
                )
                result_parts.append(converted_part)

        return "".join(result_parts)

    def __repr__(self) -> str:
        return (
            f"MarkdownPromptTemplate(name='{self.name}', "
            f"file='{self.template_file}', "
            f"variables={self.input_variables})"
        )
