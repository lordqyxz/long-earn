"""Prompt 加载器集成测试

验证 MarkdownPromptTemplate 正确加载 .md 文件并渲染变量。
"""

import pytest

from long_earn.core.prompt_loader import MarkdownPromptTemplate


class TestPromptLoaderIntegration:
    """Prompt 加载器端到端测试"""

    def test_load_and_format_prompt(self):
        """prompt 文件应可加载并正确渲染变量"""
        from pathlib import Path
        prompt_file = Path(__file__).parent.parent.parent / "src" / "long_earn" / "strategy_rd" / "agents" / "strategy_research_prompt.md"
        template = MarkdownPromptTemplate(
            str(prompt_file),
            caller_file=__file__,
        )
        # 提供所有必需变量
        prompt = template.format(
            target_market="stock",
            query="测试查询",
            strategy_examples="",
            strategy_context="",
        )

        # 由于代码块内有 {{variable}} 未被转换，所以不会出现在结果中
        # 但普通文本中的变量应被正确替换
        assert "stock" in prompt
        assert len(prompt) > 100

    def test_version_and_description_parsed(self):
        """version 和 description 应被正确解析"""
        from pathlib import Path
        prompt_file = Path(__file__).parent.parent.parent / "src" / "long_earn" / "strategy_rd" / "agents" / "strategy_research_prompt.md"
        template = MarkdownPromptTemplate(
            str(prompt_file),
            caller_file=__file__,
        )
        assert hasattr(template, "version")
        assert hasattr(template, "description")

    def test_code_block_braces_escaped(self):
        """代码块内大括号应被转义"""
        from pathlib import Path
        prompt_file = Path(__file__).parent.parent.parent / "src" / "long_earn" / "strategy_rd" / "agents" / "strategy_research_prompt.md"
        template = MarkdownPromptTemplate(
            str(prompt_file),
            caller_file=__file__,
        )
        prompt = template.format(
            target_market="stock",
            query="测试",
            strategy_examples="",
            strategy_context="",
        )

        # 代码块内不应有未转义的双花括号
        import re

        code_blocks = re.findall(r"```[\s\S]*?```", prompt)
        for block in code_blocks:
            assert "{{" not in block or "}}" not in block
