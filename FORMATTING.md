# 代码格式化配置说明

本项目已配置为使用 PyCharm 风格的代码格式化。

## 配置工具

1. **Black** - Python 代码格式化工具
   - 行宽：88 字符（PyCharm 默认）
   - Python 版本：3.11
   - 自动处理引号、空格、括号等

2. **isort** - Python 导入排序工具
   - 与 Black 兼容的配置文件
   - 自动排序和格式化导入语句
   - 支持项目自定义模块识别

3. **.editorconfig** - 编辑器配置
   - 缩进：4 个空格
   - 字符集：UTF-8
   - 行尾符：LF
   - 自动删除行尾空格

4. **pre-commit** - Git 提交前钩子
   - 自动运行 Black 和 isort
   - 检查文件格式

## 使用方法

### 手动格式化代码

```bash
# 使用格式化脚本
./format.sh

# 或者单独运行
uv run black src/ tests/
uv run isort src/ tests/
```

### 检查代码格式

```bash
# 检查 Black 格式
uv run black --check src/ tests/

# 检查 isort 格式
uv run isort --check-only src/ tests/
```

### 安装 pre-commit 钩子

如果 pre-commit 钩子未自动安装，可以手动运行：

```bash
uv run pre-commit install
```

安装后，每次 git commit 时会自动格式化代码。

## PyCharm 集成

如果你使用 PyCharm，可以配置以下设置以保持一致：

1. **文件监视器**（File Watchers）：
   - 添加 Black 文件监视器
   - 添加 isort 文件监视器
   - 配置为保存时自动运行

2. **代码风格设置**：
   - 行宽：88
   - 缩进：4 个空格
   - 导入排序：遵循 isort 规则

## 配置文件

所有配置都在 `pyproject.toml` 中：

```toml
[tool.black]
line-length = 88
target-version = ['py311']

[tool.isort]
profile = "black"
line_length = 88
lines_after_imports = -1  # 自动检测
```

## 注意事项

- Black 和 isort 会协同工作，可能需要运行两次才能达到稳定状态
- pre-commit 钩子会在提交前自动格式化，如果格式不正确会阻止提交
- 建议在保存文件时自动运行格式化工具
