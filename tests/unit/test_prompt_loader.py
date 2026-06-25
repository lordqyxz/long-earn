"""提示词加载服务测试"""

from pathlib import Path

from long_earn.core.prompt_loader import MarkdownPromptTemplate


class TestMarkdownPromptTemplate:
    def test_auto_extract_variables(self, tmp_path: Path):
        prompt_file = tmp_path / "test.md"
        prompt_file.write_text(
            "分析 ${stock} 在 ${date} 的数据，使用 ${market} 市场",
            encoding="utf-8",
        )
        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        prompt = MarkdownPromptTemplate("test.md", caller_file=str(caller_file))
        assert set(prompt.input_variables) == {"stock", "date", "market"}

    def test_format_prompt(self, tmp_path: Path):
        prompt_file = tmp_path / "test.md"
        prompt_file.write_text("你好，${name}！欢迎来到${place}。", encoding="utf-8")
        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        prompt = MarkdownPromptTemplate("test.md", caller_file=str(caller_file))
        formatted = prompt.format(name="张三", place="北京")
        assert formatted == "你好，张三！欢迎来到北京。"

    def test_code_block_no_interference(self, tmp_path: Path):
        prompt_file = tmp_path / "test.md"
        prompt_file.write_text(
            "分析 ${query}\n```python\ndata = {'key': 'value'}\n```\n结果",
            encoding="utf-8",
        )
        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        prompt = MarkdownPromptTemplate("test.md", caller_file=str(caller_file))
        assert "query" in prompt.input_variables

        formatted = prompt.format(query="股票")
        assert "股票" in formatted
        assert "data" in formatted
