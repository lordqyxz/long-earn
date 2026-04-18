"""提示词加载服务测试模块

测试 MarkdownPromptTemplate 和 PromptLoaderService 的功能。

运行测试：
    uv run pytest tests/unit/test_prompt_loader.py -v
"""

from pathlib import Path

import pytest

from long_earn.core.prompt_loader import (
    MarkdownPromptTemplate,
    PromptLoaderService,
)


class TestMarkdownPromptTemplate:
    """测试 MarkdownPromptTemplate 类"""

    def test_load_from_same_level(self, tmp_path: Path):
        """测试从同级目录加载提示词"""
        # 创建测试文件
        prompt_file = tmp_path / "test_prompt.md"
        prompt_file.write_text("你好，{name}！", encoding="utf-8")

        # 创建调用者文件
        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        # 加载提示词（使用相对路径）
        prompt = MarkdownPromptTemplate(
            "test_prompt.md",
            ["name"],
            str(caller_file),
        )

        assert prompt.template == "你好，{name}！"
        assert prompt.input_variables == ["name"]

    def test_load_from_prompts_subdir(self, tmp_path: Path):
        """测试从 prompts 子目录加载"""
        # 创建目录结构
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()

        # 创建提示词文件
        prompt_file = prompts_dir / "test_prompt.md"
        prompt_file.write_text("分析股票：{stock_code}", encoding="utf-8")

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
            "分析 {stock} 在 {date} 的数据，使用 {market} 市场", encoding="utf-8"
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
        prompt_file.write_text("你好，{name}！欢迎来到{place}。", encoding="utf-8")

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
        prompt_file.write_text("市场：{market},股票：{stock}", encoding="utf-8")

        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        prompt = MarkdownPromptTemplate(
            "test.md",
            caller_file=str(caller_file),
            partial_variables={"market": "A 股"},
        )

        formatted = prompt.format(stock="贵州茅台")
        # LangChain 的 PromptTemplate 会保留原始模板中的逗号，不会自动添加空格
        assert "市场：A 股" in formatted and "股票：贵州茅台" in formatted

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
        prompt_file.write_text("测试", encoding="utf-8")

        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        prompt = MarkdownPromptTemplate(
            "test.md",
            caller_file=str(caller_file),
        )

        repr_str = repr(prompt)
        assert "MarkdownPromptTemplate" in repr_str
        assert "test" in repr_str


class TestPromptLoaderService:
    """测试 PromptLoaderService 类"""

    def test_register_and_get(self, tmp_path: Path):
        """测试注册和获取提示词"""
        # 创建提示词文件
        prompt_file = tmp_path / "test_prompt.md"
        prompt_file.write_text("测试：{query}", encoding="utf-8")

        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        # 创建服务
        service = PromptLoaderService()

        # 注册
        service.register(
            name="test_prompt",
            caller_file=str(caller_file),
            version="1.0.0",
            description="测试提示词",
        )

        # 获取
        prompt = service.get("test_prompt")
        assert prompt.name == "test_prompt"
        assert "query" in prompt.input_variables

    def test_duplicate_register(self, tmp_path: Path):
        """测试重复注册抛出异常"""
        prompt_file = tmp_path / "test.md"
        prompt_file.write_text("测试", encoding="utf-8")

        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        service = PromptLoaderService()
        service.register("test", str(caller_file))

        with pytest.raises(ValueError):
            service.register("test", str(caller_file))

    def test_get_nonexistent(self):
        """测试获取不存在的提示词抛出异常"""
        service = PromptLoaderService()

        with pytest.raises(KeyError):
            service.get("nonexistent")

    def test_list_prompts(self, tmp_path: Path):
        """测试列出所有提示词"""
        # 创建两个提示词文件
        for name in ["prompt1", "prompt2"]:
            prompt_file = tmp_path / f"{name}.md"
            prompt_file.write_text(f"内容 {name}", encoding="utf-8")

        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        service = PromptLoaderService()
        service.register("prompt1", str(caller_file), description="第一个")
        service.register("prompt2", str(caller_file), description="第二个")

        prompts = service.list_prompts()

        assert len(prompts) == 2
        assert prompts[0]["name"] == "prompt1"
        assert prompts[1]["name"] == "prompt2"

    def test_unregister(self, tmp_path: Path):
        """测试注销提示词"""
        prompt_file = tmp_path / "test.md"
        prompt_file.write_text("测试", encoding="utf-8")

        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        service = PromptLoaderService()
        service.register("test", str(caller_file))

        assert "test" in service

        service.unregister("test")

        assert "test" not in service
        with pytest.raises(KeyError):
            service.get("test")

    def test_load_all(self, tmp_path: Path):
        """测试批量加载"""
        # 创建多个提示词文件
        for name in ["prompt1", "prompt2", "prompt3"]:
            prompt_file = tmp_path / f"{name}.md"
            prompt_file.write_text(f"内容 {name}", encoding="utf-8")

        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        service = PromptLoaderService()
        service.load_all(
            [
                {"name": "prompt1", "caller_file": str(caller_file)},
                {"name": "prompt2", "caller_file": str(caller_file)},
                {"name": "prompt3", "caller_file": str(caller_file)},
            ]
        )

        assert len(service.list_prompts()) == 3
        assert "prompt1" in service
        assert "prompt2" in service
        assert "prompt3" in service

    def test_contains(self, tmp_path: Path):
        """测试包含检查"""
        prompt_file = tmp_path / "test.md"
        prompt_file.write_text("测试", encoding="utf-8")

        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        service = PromptLoaderService()
        service.register("test", str(caller_file))

        assert "test" in service
        assert "nonexistent" not in service

    def test_getitem(self, tmp_path: Path):
        """测试下标访问"""
        prompt_file = tmp_path / "test.md"
        prompt_file.write_text("测试：{x}", encoding="utf-8")

        caller_file = tmp_path / "caller.py"
        caller_file.write_text("# caller", encoding="utf-8")

        service = PromptLoaderService()
        service.register("test", str(caller_file))

        prompt = service["test"]
        assert prompt.name == "test"


class TestIntegration:
    """集成测试"""

    def test_full_workflow(self, tmp_path: Path):
        """测试完整工作流程"""
        # 创建目录结构
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()

        # 创建提示词文件
        prompt_file = prompts_dir / "analysis.md"
        prompt_file.write_text(
            """# 分析提示词

## 任务
分析股票 {stock_code} 在 {market} 市场的表现

## 指标
- 收益率：{return_threshold}
- 风险等级：{risk_level}
""",
            encoding="utf-8",
        )

        # 创建调用者文件
        caller_file = tmp_path / "analyst.py"
        caller_file.write_text("# analyst", encoding="utf-8")

        # 使用服务
        service = PromptLoaderService()
        service.register(
            name="analysis",
            caller_file=str(caller_file),
            template_file="prompts/analysis.md",
            version="1.0.0",
            description="股票分析提示词",
        )

        # 获取并格式化
        prompt = service.get("analysis")
        formatted = prompt.format(
            stock_code="600519",
            market="A 股",
            return_threshold=0.15,
            risk_level="中",
        )

        assert "600519" in formatted
        assert "A 股" in formatted
        assert "0.15" in formatted
        assert "中" in formatted


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
