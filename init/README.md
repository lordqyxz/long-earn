# 知识库初始化文件

本文件夹包含系统启动时自动加载到 Qdrant 向量数据库的知识库文件。

## 文件格式

支持的文件格式：
- `.md` - Markdown 文件
- `.txt` - 纯文本文件
- `.py` - Python 文件（将提取文档字符串）

## 加载规则

1. 系统启动时自动扫描此文件夹
2. 检查 Qdrant 中是否存在对应的 Collection
3. 如果 Collection 不存在或为空，则加载所有文件
4. 如果已存在，则跳过加载

## Markdown 切分策略

系统使用 **MarkdownHeadingSplitter** 类处理 Markdown 文件，目标是每个词条作为一个独立的 Document。

### 切分原则

- **h1**: 文档标题
- **h2**: 一级类别 (如 "一、基础指标类")
- **h3**: 二级类别 (如 "1.1 价格指标")
- **h4**: 词条标题 (如 "收盘价")

### 元数据

每个 Document 携带以下元数据：

| 字段 | 说明 |
|------|------|
| `source_file` | 来源文件名 |
| `term` | 词条名称 (如 "夏普比率") |
| `category` | 所属类别 (如 "四、风险指标类") |
| `section_level` | 标题级别 (3=h3, 4=h4) |

### 搜索接口

`search_knowledge` 函数支持多种过滤方式：

```python
from long_earn.tools.store import search_knowledge

# 基础搜索
results = search_knowledge("夏普比率", k=3)

# 按类别过滤 (词汇表)
results = search_knowledge("策略优化", k=3, categories=["四、风险指标类"])

# 按词条名称过滤
results = search_knowledge("收益", k=3, terms=["夏普比率", "Beta"])

# 按源文件过滤 (代码文档)
results = search_knowledge("数据获取", k=3, source_files=["01_data.md", "02_strategy.md"])
```

## 节点知识配置

不同工作流节点配置了不同的知识检索方式：

| 节点 | 检索方式 | 用途 |
|------|----------|------|
| **research** | 按类别过滤 | 策略概念、指标知识 |
| **reflection** | 按类别过滤 | 风险评估、策略优化知识 |
| **optimize** | 按类别过滤 | 优化方法知识 |
| **develop** | 按源文件过滤 | Qlib 代码实现知识 |

### develop 节点检索的文件

- `01_data.md` - 数据获取
- `02_strategy.md` - 策略基类
- `03_signals.md` - 信号生成
- `04_backtest.md` - 回测配置
- `05_metrics.md` - 绩效指标
- `06_errors.md` - 错误处理
- `07_example.md` - 代码示例

## 词汇表文件规范

词汇表文件 (如 `08_glossary.md`) 使用统一的标题层级：

```markdown
# 文档标题 (h1)

## 一级类别 (h2)

### 二级类别 (h3)

#### 词条名称 (h4)
- **解释**: 词条解释
- **计算方法**: 计算公式或方法
```

## 当前文件清单

| 文件 | 说明 |
|------|------|
| `01_data.md` | Qlib 数据获取指南 |
| `02_strategy.md` | 策略开发指南 |
| `03_signals.md` | 信号生成 |
| `04_backtest.md` | 回测框架 |
| `05_metrics.md` | 策略指标 |
| `06_errors.md` | 常见错误处理 |
| `07_example.md` | 示例代码 |
| `08_glossary.md` | 金融量化词汇表 (约 80+ 词条) |
| `README.md` | 本文件 |
