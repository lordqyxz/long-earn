#!/bin/bash
# 代码格式化和检查脚本 - 使用 ruff

echo "1. 运行 ruff format 格式化..."
uv run ruff format src/ tests/

echo "2. 运行 ruff check 检查并自动修复..."
uv run ruff check --fix src/ tests/

echo "3. 验证检查结果..."
if uv run ruff check src/ tests/; then
    echo "✅ 代码格式化和检查完成！所有文件符合规范。"
else
    echo "❌ 仍有问题，请检查输出。"
    exit 1
fi