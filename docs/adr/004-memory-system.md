# ADR-004: 基于 numpy/pandas 的三级记忆系统

日期: 2025-05
状态: Superseded by [ADR-007](007-unified-substance-architecture.md)（2026-06）

## 背景

原系统依赖 Qdrant 向量数据库（`langchain-qdrant` + `qdrant-client`）实现知识检索，但存在以下问题：
- **部署复杂度高**: 需要额外运行 Qdrant 服务，与"本地优先"理念冲突
- **依赖臃肿**: Qdrant 客户端和 HTTP/2 协议库合计 ~30MB，占项目依赖一半以上
- **过度设计**: 项目事实量级为数百到数千条，无需分布式向量数据库

此外，策略研发 Agent 需要更丰富的记忆能力（经验积累、关系推理、反思提炼），单一向量检索无法满足。

## 决策

设计并实现三组件本地记忆系统 (`src/long_earn/memory/`)，替代 Qdrant，零外部依赖：

### 1. TfidfVectorizer + Cosine Similarity (`tfidf.py`)
- 纯 numpy 实现，支持混合中英文分词（正则匹配）
- L2 归一化 TF + 平滑 IDF，与 sklearn 行为一致
- 可配置 max_features 控制词汇表大小

### 2. RelationGraph (`graph.py`)
- 基于 numpy 邻接矩阵的有向加权关系图
- 支持 BFS 多跳关联查询 (`get_related(depth=n)`)
- O(1) 邻接查询，适合小规模知识图谱（<10000 节点）

### 3. MemoryStore (`store.py`)
统一入口，集成 TF-IDF 检索 + 关系图 + 事实管理：
- Markdown 标题感知切分（基于 `#` 层级，自动生成面包屑面包屑路径）
- 重叠滑动窗口切分（chunk_overlap 防止语义断裂）
- `np.savez_compressed` 持久化，跨会话复用

### 服务接口 (`MemoryService` Protocol)
遵循 Letta/MemGPT 三级记忆模式，定义统一接口：

| 层级 | 用途 | 存储 |
|------|------|------|
| Working | 当前会话上下文 | 短期内存 |
| Core | 持久事实、规则、偏好 | MemoryStore |
| Archival | 历史经验、过期规则 | MemoryStore（低优先级） |

核心方法：`remember` / `recall` / `search` / `reflect` / `relate` / `initialize`

## 理由

1. **简洁性**: 纯 numpy/pandas，与回测引擎共享技术底座，无外部服务依赖
2. **充分性**: 千级事实的语义搜索，TF-IDF 精度足够；关系图满足策略经验关联需求
3. **可维护性**: 代码量 < 500 行，调试和扩展成本极低
4. **渐进式三级记忆**: 支持未来扩展到 MemGPT 风格的自动记忆压缩和反思

## 后果

- 移除了 6 个 Qdrant 相关包（~30MB）
- 语义检索精度从 dense embedding 降为 TF-IDF 稀疏向量，对长尾查询的召回可能下降
- 不支持 Qdrant 的标量索引过滤（当前用 Python 循环过滤替代，千级数据量可接受）
- 关系图为稠密矩阵存储（O(n²)），节点数 > 10000 时需考虑稀疏矩阵优化
- 未来如需语义检索精度，可替换为 `sentence-transformers` 或 `fasttext` 生成 embedding，接口层无需变更
