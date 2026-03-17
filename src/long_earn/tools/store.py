import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from langchain_community.document_loaders import (
    TextLoader,
    PythonLoader,
)
from langchain_ollama import OllamaEmbeddings
from langchain_qdrant import QdrantVectorStore

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from long_earn.tools.md_splitter import MarkdownHeadingSplitter
from long_earn.utils.logger import LOGGER

COLLECTION_NAME = "knowledge_base"
INIT_DIR = Path(__file__).parent.parent.parent / "init"

vector_store: Optional[QdrantVectorStore] = None
_client: Optional[QdrantClient] = None


def _get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(
            url=os.getenv("QDRANT_URL", ":memory:"),
            api_key=os.getenv("QDRANT_KEY", None),
        )
    return _client


def _get_embeddings():
    return OllamaEmbeddings(model=os.getenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b"))


from langchain_core.documents import Document



def _load_document(file_path: Path) -> Optional[list]:
    """根据文件类型加载文档"""
    suffix = file_path.suffix.lower()

    try:
        if suffix == ".md":
            return _load_markdown_file(file_path)
        elif suffix == ".txt":
            loader = TextLoader(str(file_path), encoding="utf-8")
        elif suffix == ".py":
            loader = PythonLoader(str(file_path))
        else:
            LOGGER.warning(f"不支持的文件类型: {suffix}")
            return None

        return loader.load()
    except Exception as e:
        LOGGER.error(f"加载文件失败 {file_path}: {e}")
        return None


def _load_markdown_file(file_path: Path) -> List[Document]:
    """加载 Markdown 文件，使用标题感知切分"""
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        LOGGER.error(f"读取文件失败 {file_path}: {e}")
        return []

    splitter = MarkdownHeadingSplitter(
        source_file=file_path.name,
        chunk_size=1500,
        chunk_overlap=200,
    )
    documents = splitter.split_text(content)

    if not documents:
        LOGGER.warning(f"未能从 {file_path.name} 解析出任何内容")

    return documents


def _should_load_collection(client: QdrantClient, collection_name: str) -> bool:
    """检查是否需要加载数据"""
    try:
        if not client.collection_exists(collection_name):
            LOGGER.info(f"Collection '{collection_name}' 不存在，需要创建并加载数据")
            return True

        collection_info = client.get_collection(collection_name)
        points_count = collection_info.points_count

        if points_count == 0:
            LOGGER.info(f"Collection '{collection_name}' 为空，需要加载数据")
            return True

        LOGGER.info(
            f"Collection '{collection_name}' 已存在 ({points_count} 条记录)，跳过加载"
        )
        return False
    except Exception as e:
        LOGGER.warning(f"检查 Collection 状态失败: {e}，将尝试加载")
        return True


def _init_knowledge_base():
    """初始化知识库 - 扫描 init 文件夹并加载到 Qdrant"""

    client = _get_client()
    embeddings = _get_embeddings()

    if not _should_load_collection(client, COLLECTION_NAME):
        return

    if not INIT_DIR.exists():
        LOGGER.warning(f"初始化目录不存在: {INIT_DIR}")
        return

    supported_extensions = {".md", ".txt", ".py"}
    files = [
        f
        for f in INIT_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in supported_extensions
    ]

    if not files:
        LOGGER.warning(f"初始化目录中没有找到支持的文件: {INIT_DIR}")
        return

    all_documents = []
    for file_path in files:
        LOGGER.info(f"加载文件: {file_path.name}")
        docs = _load_document(file_path)
        if docs:
            all_documents.extend(docs)

    if not all_documents:
        LOGGER.warning("没有加载任何文档")
        return

    LOGGER.info(f"正在将 {len(all_documents)} 个文档片段加载到 Qdrant...")

    try:
        vector_size = len(embeddings.embed_query("sample text"))
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
    except Exception:
        pass

    vector_store = QdrantVectorStore(
        client=client,
        collection_name=COLLECTION_NAME,
        embedding=embeddings,
    )

    vector_store.add_documents(all_documents)
    LOGGER.info(f"知识库初始化完成，共加载 {len(all_documents)} 个文档片段")


def get_vector_store() -> QdrantVectorStore:
    """获取向量存储实例"""
    global vector_store

    if vector_store is None:
        client = _get_client()
        embeddings = _get_embeddings()

        vector_store = QdrantVectorStore(
            client=client,
            collection_name=COLLECTION_NAME,
            embedding=embeddings,
        )

    return vector_store


def search_knowledge(
    query: str,
    k: int = 3,
    categories: Optional[List[str]] = None,
    terms: Optional[List[str]] = None,
    source_files: Optional[List[str]] = None,
) -> List[str]:
    """搜索知识库

    Args:
        query: 搜索查询
        k: 返回结果数量
        categories: 可选，按类别过滤 (如 ["四、风险指标类", "五、量化策略类"])
        terms: 可选，按词条名称过滤 (如 ["夏普比率", "Beta"])
        source_files: 可选，按源文件过滤 (如 ["01_data.md", "02_strategy.md"])

    Returns:
        搜索结果列表
    """
    try:
        store = get_vector_store()

        results = store.similarity_search(query, k=k * 3)

        search_results = []
        for doc in results:
            meta = doc.metadata

            if categories:
                category = meta.get("category", "")
                if not any(cat in category for cat in categories):
                    continue

            if terms:
                term = meta.get("term", "")
                if not any(t in term for t in terms):
                    continue

            if source_files:
                source = meta.get("source_file", "")
                if source not in source_files:
                    continue

            source = meta.get("source_file", "unknown")
            term_name = meta.get("term", "")
            category = meta.get("category", "")
            content = doc.page_content[:500]

            header = f"【来源: {source}"
            if term_name:
                header += f" | 词条: {term_name}"
            if category:
                header += f" | 类别: {category}"
            header += "】"

            search_results.append(f"{header}\n{content}\n")

            if len(search_results) >= k:
                break

        return search_results
    except Exception as e:
        LOGGER.error(f"搜索知识库失败: {e}")
        return []


def init_system():
    """系统初始化函数 - 启动时调用"""
    import os

    LOGGER.info("开始系统初始化...")
    _init_knowledge_base()
    LOGGER.info("系统初始化完成")


def save_experience(
    strategy_code: str,
    strategy_name: str,
    design_rationale: str,
    backtest_result: dict,
    reflection: str,
    error_history: Optional[List[dict]] = None,
) -> bool:
    """保存策略开发经验到知识库

    Args:
        strategy_code: 可运行的策略代码
        strategy_name: 策略名称
        design_rationale: 设计思路
        backtest_result: 回测结果
        reflection: 反思结论
        error_history: 错误历史（可选）

    Returns:
        是否保存成功
    """
    try:
        client = _get_client()

        content = f"""# 策略经验: {strategy_name}

## 设计思路
{design_rationale}

## 策略代码
```python
{strategy_code}
```

## 回测结果
```json
{json.dumps(backtest_result, ensure_ascii=False, indent=2)}
```

## 反思结论
{reflection}
"""

        if error_history:
            content += f"""
## 错误历史
{json.dumps(error_history, ensure_ascii=False, indent=2)}
"""

        content += f"""
---
**创建时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

        doc = Document(
            page_content=content,
            metadata={
                "source_file": "experience.md",
                "term": strategy_name,
                "category": "策略经验",
                "section_level": 1,
                "experience_type": "strategy",
                "backtest_metrics": backtest_result.get("metrics", {}),
            },
        )

        vector_store = get_vector_store()
        vector_store.add_documents([doc])

        LOGGER.info(f"策略经验已保存: {strategy_name}")
        return True

    except Exception as e:
        LOGGER.error(f"保存经验失败: {e}")
        return False


def search_experience(
    query: str,
    k: int = 3,
    min_sharpe: Optional[float] = None,
) -> List[dict]:
    """搜索历史策略经验

    Args:
        query: 搜索查询
        k: 返回结果数量
        min_sharpe: 最小夏普比率过滤

    Returns:
        经验列表，每条包含 code, rationale, metrics
    """
    try:
        store = get_vector_store()
        results = store.similarity_search(query, k=k * 2)

        experiences = []
        for doc in results:
            meta = doc.metadata

            if meta.get("experience_type") != "strategy":
                continue

            if min_sharpe:
                metrics = meta.get("backtest_metrics", {})
                sharpe = metrics.get("sharpe_ratio", 0) or metrics.get("sharpe", 0)
                if sharpe < min_sharpe:
                    continue

            content = doc.page_content

            code_match = re.search(r"```python\n(.*?)```", content, re.DOTALL)
            code = code_match.group(1).strip() if code_match else ""

            rationale_match = re.search(
                r"## 设计思路\n(.*?)## 策略代码", content, re.DOTALL
            )
            rationale = rationale_match.group(1).strip() if rationale_match else ""

            experiences.append(
                {
                    "name": meta.get("term", ""),
                    "code": code,
                    "rationale": rationale,
                    "metrics": meta.get("backtest_metrics", {}),
                }
            )

            if len(experiences) >= k:
                break

        return experiences

    except Exception as e:
        LOGGER.error(f"搜索经验失败: {e}")
        return []
