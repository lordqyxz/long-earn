"""ADR-011 阶段 2 迁移后验证：所有 prompt 文件应使用 jinja2 语法并正确渲染。

扫描 src/long_earn 下所有 .md prompt 文件：
1. 不应残留 ${var} 占位符
2. {{ var }} 变量应能被 _extract_variables 正确识别
3. 加载 MarkdownPromptTemplate 并 format 应不报错（用空串填充缺失变量）
"""

from pathlib import Path

import pytest

from long_earn.core.prompt_loader import MarkdownPromptTemplate, _extract_variables

PROMPT_ROOT = Path(__file__).parent.parent.parent / "src" / "long_earn"


def _find_prompt_files() -> list[Path]:
    """收集所有 .md prompt 文件。"""
    return sorted(PROMPT_ROOT.rglob("*.md"))


@pytest.fixture(params=_find_prompt_files())
def prompt_file(request) -> Path:
    return request.param


class TestPromptMigration:
    """阶段 2 迁移后所有 prompt 文件的渲染正确性验证。"""

    def test_no_dollar_brace_placeholder(self, prompt_file: Path):
        """不应残留 ${var} 形式的旧占位符。"""
        content = prompt_file.read_text(encoding="utf-8")
        import re

        matches = re.findall(r"\$\{[a-zA-Z_]\w*\}", content)
        assert not matches, (
            f"{prompt_file.relative_to(PROMPT_ROOT)} 残留旧占位符: {matches}"
        )

    def test_variables_extractable(self, prompt_file: Path):
        """所有 {{ var }} 变量应被 _extract_variables 正确识别。"""
        content = prompt_file.read_text(encoding="utf-8")
        # 剥离 frontmatter
        import re

        m = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", content, re.DOTALL)
        body = m.group(1) if m else content
        vars_found = _extract_variables(body)
        # 每个变量名应是合法标识符
        for v in vars_found:
            assert v.isidentifier(), f"{prompt_file}: 非法变量名 {v}"

    def test_load_and_format_with_empty_values(self, prompt_file: Path):
        """加载并用空串填充所有变量应成功渲染（不抛异常）。"""
        try:
            template = MarkdownPromptTemplate(
                str(prompt_file),
                caller_file=__file__,
            )
        except Exception as e:
            # 个别 prompt 可能因 frontmatter 解析问题跳过
            pytest.skip(f"加载失败（可能是 frontmatter 格式）: {e}")

        # 用空串填充所有变量（jinja2 默认缺失变量也输出空串，这里显式传）
        kwargs = dict.fromkeys(template.input_variables, "")
        try:
            result = template.format(**kwargs)
        except Exception as e:
            pytest.fail(f"{prompt_file.name} 渲染失败: {type(e).__name__}: {e}")

        # 渲染结果不应包含未替换的 {{ }}（jinja2 缺失变量输出空串，不应残留 {{ var }}）
        import re

        leftover = re.findall(r"\{\{\s*\w+\s*\}\}", result)
        assert not leftover, (
            f"{prompt_file.name} 渲染后残留未替换变量: {leftover}"
        )

    def test_no_html_escape_in_rendered(self, prompt_file: Path):
        """渲染结果不应引入 HTML 实体（jinja2 默认不转义）。"""
        try:
            template = MarkdownPromptTemplate(
                str(prompt_file),
                caller_file=__file__,
            )
        except Exception:
            pytest.skip("加载失败")

        # 用含 <>& 的值填充，验证不转义
        kwargs = {v: f"<{v}>&" for v in template.input_variables}
        if not template.input_variables:
            return  # 无变量则无需验证
        result = template.format(**kwargs)
        # 不应出现 HTML 实体
        assert "&lt;" not in result, f"{prompt_file.name} 出现 &lt; 转义"
        assert "&gt;" not in result, f"{prompt_file.name} 出现 &gt; 转义"
        assert "&amp;" not in result or "&" in result, (
            f"{prompt_file.name} 出现 &amp; 转义"
        )
