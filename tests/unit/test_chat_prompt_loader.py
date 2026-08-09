"""MarkdownChatPromptTemplate 单元测试 — ADR-011 Phase 4"""

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from long_earn.core.chat_prompt_loader import MarkdownChatPromptTemplate


def _write(tmp_path: Path, name: str, content: str) -> tuple[Path, Path]:
    """在 tmp_path 下写入 prompt 文件和一个占位 caller.py，返回 (prompt_path, caller_path)。"""
    prompt_file = tmp_path / name
    prompt_file.write_text(content, encoding="utf-8")
    caller_file = tmp_path / "caller.py"
    caller_file.write_text("# caller\n", encoding="utf-8")
    return prompt_file, caller_file


class TestSystemHumanSplit:
    """test_system_human_split：frontmatter 有 messages.system + messages.human，
    format_messages 返回 [SystemMessage, HumanMessage]。"""

    def test_system_human_split(self, tmp_path: Path):
        content = """---
version: 2.0.0
description: 测试 system/human 拆分
messages:
  system: |
    你是{{ role }}分析师
  human: |
    股票数据：{{ stock_data }}
    请分析。
---
"""
        prompt_file, caller_file = _write(tmp_path, "split.md", content)
        tpl = MarkdownChatPromptTemplate(str(prompt_file), caller_file=str(caller_file))

        msgs = tpl.format_messages(role="价值", stock_data="600519")

        assert len(msgs) == 2
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[1], HumanMessage)
        assert "价值" in msgs[0].content
        assert "600519" in msgs[1].content
        assert "请分析" in msgs[1].content


class TestJinja2Syntax:
    """test_jinja2_syntax：模板用 {{ var }}，变量正确渲染，不 HTML 转义。"""

    def test_jinja2_syntax(self, tmp_path: Path):
        content = """---
messages:
  system: |
    角色：{{ role }}
  human: |
    数据：{{ data }}
---
"""
        prompt_file, caller_file = _write(tmp_path, "jinja.md", content)
        tpl = MarkdownChatPromptTemplate(str(prompt_file), caller_file=str(caller_file))

        msgs = tpl.format_messages(role="<a> & x", data="<strategy> & y")

        assert msgs[0].content == "角色：<a> & x"
        assert msgs[1].content == "数据：<strategy> & y"
        # 关键：不 HTML 转义
        assert "&amp;" not in msgs[0].content
        assert "&lt;" not in msgs[1].content


class TestNoMessagesFieldFallback:
    """test_no_messages_field_fallback：无 messages frontmatter 时退化为单 HumanMessage。"""

    def test_no_messages_field_fallback(self, tmp_path: Path):
        content = """---
version: 1.0.0
description: 无 messages 字段
---

分析 {{ stock }} 的数据，市场：{{ market }}。"""
        prompt_file, caller_file = _write(tmp_path, "fallback.md", content)
        tpl = MarkdownChatPromptTemplate(str(prompt_file), caller_file=str(caller_file))

        msgs = tpl.format_messages(stock="600519", market="A 股")

        assert len(msgs) == 1
        assert isinstance(msgs[0], HumanMessage)
        assert "600519" in msgs[0].content
        assert "A 股" in msgs[0].content


class TestPlaceholder:
    """test_placeholder：messages 里有 placeholder: examples 时，
    format_messages(examples=[...]) 正确注入。"""

    def test_placeholder(self, tmp_path: Path):
        content = """---
messages:
  system: |
    你是{{ role }}分析师
  placeholder: examples
  human: |
    数据：{{ data }}
---
"""
        prompt_file, caller_file = _write(tmp_path, "placeholder.md", content)
        tpl = MarkdownChatPromptTemplate(str(prompt_file), caller_file=str(caller_file))

        examples = [
            HumanMessage(content="示例问题"),
            AIMessage(content="示例回答"),
        ]
        msgs = tpl.format_messages(role="价值", data="600519", examples=examples)

        # system + 2 examples + human = 4
        assert len(msgs) == 4
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[1], HumanMessage)
        assert msgs[1].content == "示例问题"
        assert isinstance(msgs[2], AIMessage)
        assert msgs[2].content == "示例回答"
        assert isinstance(msgs[3], HumanMessage)
        assert "600519" in msgs[3].content


class TestFormatReturnsString:
    """test_format_returns_string：format() 返回拼接字符串（向后兼容）。"""

    def test_format_returns_string(self, tmp_path: Path):
        content = """---
messages:
  system: |
    你是{{ role }}
  human: |
    数据：{{ data }}
---
"""
        prompt_file, caller_file = _write(tmp_path, "fmt.md", content)
        tpl = MarkdownChatPromptTemplate(str(prompt_file), caller_file=str(caller_file))

        result = tpl.format(role="价值", data="600519")

        assert isinstance(result, str)
        # system 内容在前，human 内容在后，用换行拼接
        assert "你是价值" in result
        assert "数据：600519" in result

    def test_format_fallback_single_message(self, tmp_path: Path):
        """退化模式下 format() 等价于单模板渲染。"""
        content = """---
version: 1.0.0
---

分析 {{ stock }}。"""
        prompt_file, caller_file = _write(tmp_path, "fmt_fb.md", content)
        tpl = MarkdownChatPromptTemplate(str(prompt_file), caller_file=str(caller_file))

        result = tpl.format(stock="600519")
        assert result == "分析 600519。"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
