"""提示词加载服务测试模块

测试 MarkdownPromptTemplate 的功能。
注意：MarkdownPromptTemplate 使用 {{variable}} 双花括号语法标记变量，
在代码块外的 {{variable}} 会被转换为 {variable} 供 LangChain 处理。

运行测试：
    uv run pytest tests/unit/test_prompt_loader.py -v
"""

from pathlib import Path

import pytest

from long_earn.core.prompt_loader import MarkdownPromptTemplate


class TestMarkdownPromptTemplate:
    """测试 MarkdownPromptTemplate 类"""

    def test_load_from_same_level(self, tmp_path: Path):
        """测试从同级目录加载提示词"""
        # 创建测试文件（使用双花括号语法）
        prompt_file = tmp_path / "test_prompt.md"
        prompt_file.write_text("你好，{{name}}！", encoding="utf-8")

        # 创建调用者文件
        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        # 加载提示词（使用相对路径）
        prompt = MarkdownPromptTemplate(
            "test_prompt.md",
            ["name"],
            str(caller_file),
        )

        assert "你好" in prompt.template
        assert "name" in prompt.input_variables

    def test_load_from_prompts_subdir(self, tmp_path: Path):
        """测试从 prompts 子目录加载"""
        # 创建目录结构
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()

        # 创建提示词文件（使用双花括号语法）
        prompt_file = prompts_dir / "test_prompt.md"
        prompt_file.write_text("分析股票：{{stock_code}}", encoding="utf-8")

        # 创建调用者文件
        caller_file = tmp_path / "agent.py"
        caller_file.write_text("# agent", encoding="utf-8")

        # 加载提示词（使用相对路径）
        prompt = MarkdownPromptTemplate(
            "prompts/test_prompt.md",
            caller_file=str(caller_file),
        )

        assert "stock_code" in prompt.input_variables

    def test_auto_extract_variables(self, tmp_path: Path):
        """测试自动提取变量"""
        prompt_file = tmp_path / "test.md"
        prompt_file.write_text(
            "分析 {{stock}} 在 {{date}} 的数据，使用 {{market}} 市场",
            encoding="utf-8",
        )

        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        # 不传 input_variables，自动提取
        prompt = MarkdownPromptTemplate(
            "test.md",
            caller_file=str(caller_file),
        )

        # 验证自动提取的变量（顺序可能不同）
        assert set(prompt.input_variables) == {"stock", "date", "market"}

    def test_format_prompt(self, tmp_path: Path):
        """测试格式化提示词"""
        prompt_file = tmp_path / "test.md"
        prompt_file.write_text("你好，{{name}}！欢迎来到{{place}}。", encoding="utf-8")

        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        prompt = MarkdownPromptTemplate(
            "test.md",
            caller_file=str(caller_file),
        )

        formatted = prompt.format(name="张三", place="北京")
        assert formatted == "你好，张三！欢迎来到北京。"

    def test_partial_variables(self, tmp_path: Path):
        """测试部分变量"""
        prompt_file = tmp_path / "test.md"
        prompt_file.write_text("市场：{{market}}，股票：{{stock}}", encoding="utf-8")

        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        prompt = MarkdownPromptTemplate(
            "test.md",
            caller_file=str(caller_file),
            partial_variables={"market": "A 股"},
        )

        formatted = prompt.format(stock="贵州茅台")
        assert "市场：A 股" in formatted and "股票：贵州茅台" in formatted

    def test_code_block_escaping(self, tmp_path: Path):
        """测试代码块中的大括号被正确转义"""
        prompt_file = tmp_path / "test.md"
        prompt_file.write_text(
            "分析 {{query}}\n```python\ndata = {'key': 'value'}\n```\n结果",
            encoding="utf-8",
        )

        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        prompt = MarkdownPromptTemplate(
            "test.md",
            caller_file=str(caller_file),
        )

        # query 变量应该被提取
        assert "query" in prompt.input_variables

        # 格式化后代码块中的字典应该保留
        formatted = prompt.format(query="股票")
        assert "股票" in formatted
        assert "data" in formatted

    def test_file_not_found(self, tmp_path: Path):
        """测试文件不存在时抛出异常"""
        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        with pytest.raises(FileNotFoundError):
            MarkdownPromptTemplate(
                "nonexistent.md",
                caller_file=str(caller_file),
            )

    def test_repr(self, tmp_path: Path):
        """测试字符串表示"""
        prompt_file = tmp_path / "test.md"
        prompt_file.write_text("测试 {{query}}", encoding="utf-8")

        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        prompt = MarkdownPromptTemplate(
            "test.md",
            caller_file=str(caller_file),
        )

        repr_str = repr(prompt)
        assert "MarkdownPromptTemplate" in repr_str
        assert "test" in repr_str


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
