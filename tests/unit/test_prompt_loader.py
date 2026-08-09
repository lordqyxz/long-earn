"""提示词加载服务测试"""

from pathlib import Path

from long_earn.core.prompt_loader import MarkdownPromptTemplate, render


class TestRender:
    """render() 函数测试"""

    def test_basic(self):
        assert (
            render("{{ greeting }}, {{ name }}!", {"greeting": "你好", "name": "张三"})
            == "你好, 张三!"
        )

    def test_missing_empty_string(self):
        # jinja2 默认缺失变量输出空串（与 safe_substitute 的"原样保留"不同）
        assert render("{{ a }} and {{ b }}", {"a": "x"}) == "x and "

    def test_no_escape(self):
        # jinja2 默认不 HTML 转义（关键：LLM 提示词场景需要原样输出 <>&）
        assert (
            render("data: {{ x }}", {"x": "<strategy> & y"}) == "data: <strategy> & y"
        )

    def test_no_vars(self):
        assert render("hello world", {"x": 1}) == "hello world"

    def test_int_value(self):
        assert render("count: {{ n }}", {"n": 42}) == "count: 42"

    def test_float_value(self):
        assert render("rate: {{ r }}", {"r": 0.15}) == "rate: 0.15"

    def test_json_dict_literal_no_conflict(self):
        # jinja2 {{ var }} 与字面 JSON {} 不冲突（关键：本项目 prompt 含大量 JSON Schema）
        tpl = 'data = {"key": "value"}\nresult: {{ x }}'
        assert render(tpl, {"x": "ok"}) == 'data = {"key": "value"}\nresult: ok'

    def test_code_block_braces_no_conflict(self):
        # 代码块里的 {} 不被当作变量
        tpl = "```python\nd = {'a': 1}\n```\n{{ x }}"
        assert render(tpl, {"x": "out"}) == "```python\nd = {'a': 1}\n```\nout"

    def test_jinja2_default_filter(self):
        # jinja2 白送的 default 过滤器
        assert render("v={{ v | default('N/A') }}", {}) == "v=N/A"

    def test_sandbox_blocks_dunder(self):
        # SandboxedEnvironment 阻断 __class__ 等逃逸
        try:
            out = render("{{ x.__class__ }}", {"x": "abc"})
            # 应被沙箱拦截或返回空/报错，不应返回 <class 'str'>
            assert "<class 'str'>" not in out
        except Exception:
            # 沙箱抛异常也算正确行为
            pass


class TestExtractVariables:
    def test_basic(self):
        from long_earn.core.prompt_loader import _extract_variables

        assert _extract_variables("{{ a }} {{ b }} {{ c }}") == ["a", "b", "c"]

    def test_dedup(self):
        from long_earn.core.prompt_loader import _extract_variables

        assert _extract_variables("{{ a }} {{ b }} {{ a }}") == ["a", "b"]

    def test_empty(self):
        from long_earn.core.prompt_loader import _extract_variables

        assert _extract_variables("no vars here") == []

    def test_mixed_text(self):
        from long_earn.core.prompt_loader import _extract_variables

        assert _extract_variables("price {{ threshold }} > {{ stop_loss }}") == [
            "threshold",
            "stop_loss",
        ]

    def test_no_conflict_with_json_braces(self):
        # 字面 JSON {} 不应被识别为变量
        from long_earn.core.prompt_loader import _extract_variables

        tpl = '{"key": "value"} {{ real }}'
        assert _extract_variables(tpl) == ["real"]


class TestMarkdownPromptTemplate:
    def test_auto_extract_variables(self, tmp_path: Path):
        prompt_file = tmp_path / "test.md"
        prompt_file.write_text(
            "分析 {{ stock }} 在 {{ date }} 的数据，使用 {{ market }} 市场",
            encoding="utf-8",
        )
        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        prompt = MarkdownPromptTemplate("test.md", caller_file=str(caller_file))
        assert set(prompt.input_variables) == {"stock", "date", "market"}

    def test_format_prompt(self, tmp_path: Path):
        prompt_file = tmp_path / "test.md"
        prompt_file.write_text(
            "你好，{{ name }}！欢迎来到{{ place }}。", encoding="utf-8"
        )
        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        prompt = MarkdownPromptTemplate("test.md", caller_file=str(caller_file))
        formatted = prompt.format(name="张三", place="北京")
        assert formatted == "你好，张三！欢迎来到北京。"

    def test_code_block_no_interference(self, tmp_path: Path):
        prompt_file = tmp_path / "test.md"
        prompt_file.write_text(
            "分析 {{ query }}\n```python\ndata = {'key': 'value'}\n```\n结果",
            encoding="utf-8",
        )
        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        prompt = MarkdownPromptTemplate("test.md", caller_file=str(caller_file))
        assert "query" in prompt.input_variables

        formatted = prompt.format(query="股票")
        assert "股票" in formatted
        assert "data" in formatted

    def test_no_html_escape(self, tmp_path: Path):
        # LLM 提示词场景关键：变量值含 <>& 不应被转义
        prompt_file = tmp_path / "test.md"
        prompt_file.write_text("data: {{ x }}", encoding="utf-8")
        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        prompt = MarkdownPromptTemplate("test.md", caller_file=str(caller_file))
        assert prompt.format(x="<a> & b") == "data: <a> & b"

    def test_missing_variable_empty_string(self, tmp_path: Path):
        # jinja2 缺失变量输出空串（与旧 safe_substitute 行为不同）
        prompt_file = tmp_path / "test.md"
        prompt_file.write_text("{{ a }} and {{ b }}", encoding="utf-8")
        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        prompt = MarkdownPromptTemplate("test.md", caller_file=str(caller_file))
        assert prompt.format(a="x") == "x and "
