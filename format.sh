#!/bin/bash
# 代码格式化脚本 - PyCharm 风格

echo "正在格式化代码..."
echo "1. 运行 Black 格式化..."
uv run black src/ tests/

echo "2. 运行 isort 格式化导入..."
uv run isort src/ tests/

echo "3. 再次运行 Black 确保格式一致..."
uv run black src/ tests/

echo "4. 验证格式化结果..."
if uv run black --check src/ tests/ && uv run isort --check-only src/ tests/; then
    echo "✅ 代码格式化完成！所有文件符合 PyCharm 风格。"
else
    echo "❌ 格式化后仍有问题，请检查输出。"
    exit 1
fi
