"""render.py 纯函数渲染器测试"""

from long_earn.core.render import extract_variables, render


class TestRender:
    def test_basic(self):
        assert render("${greeting}, ${name}!", {"greeting": "你好", "name": "张三"}) == "你好, 张三!"

    def test_missing_safe(self):
        assert render("${a} and ${b}", {"a": "x"}) == "x and ${b}"

    def test_dollar_escape(self):
        assert render("price $$100", {}) == "price $100"

    def test_no_vars(self):
        assert render("hello world", {"x": 1}) == "hello world"

    def test_int_value(self):
        assert render("count: ${n}", {"n": 42}) == "count: 42"

    def test_float_value(self):
        assert render("rate: ${r}", {"r": 0.15}) == "rate: 0.15"


class TestExtractVariables:
    def test_basic(self):
        assert extract_variables("${a} ${b} ${c}") == ["a", "b", "c"]

    def test_dedup(self):
        assert extract_variables("${a} ${b} ${a}") == ["a", "b"]

    def test_empty(self):
        assert extract_variables("no vars here") == []

    def test_mixed_text(self):
        assert extract_variables("price ${threshold} > ${stop_loss}") == ["threshold", "stop_loss"]

    def test_dollar_escape_not_extracted(self):
        assert extract_variables("$$100 ${real}") == ["real"]
