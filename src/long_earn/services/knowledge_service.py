"""知识存储服务实现

封装 Qdrant 向量数据库的操作，提供统一的接口。
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_community.document_loaders import PythonLoader, TextLoader
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from long_earn.config import RuntimeContext
from long_earn.services import KnowledgeService
from long_earn.tools.md_splitter import MarkdownHeadingSplitter


class KnowledgeServiceImpl(KnowledgeService):
    """知识存储服务实现

    参考 LangGraph Runtime 实践：
    1. 依赖通过 context 传递
    2. 资源懒加载
    3. 支持生命周期管理
    """

    COLLECTION_NAME = "knowledge_base"

    def __init__(self, context: "RuntimeContext"):
        """初始化知识服务

        Args:
            context: 运行时上下文
        """
        self.context = context
        self.config = context.config
        self.logger = context.logger
        self._client: QdrantClient | None = None
        self._embeddings: OllamaEmbeddings | None = None
        self._vector_store: QdrantVectorStore | None = None
        self._initialized = False

    def _get_client(self) -> QdrantClient:
        """获取 Qdrant 客户端（懒加载）

        Returns:
            Qdrant 客户端实例
        """
        if self._client is None:
            self._client = QdrantClient(
                url=self.config.qdrant_url,
                api_key=self.config.qdrant_api_key,
            )
        return self._client

    def _get_embeddings(self) -> OllamaEmbeddings:
        """获取嵌入模型（懒加载）

        Returns:
            嵌入模型实例
        """
        if self._embeddings is None:
            self._embeddings = OllamaEmbeddings(model=self.config.embedding_model)
        return self._embeddings

    def _get_vector_store(self) -> QdrantVectorStore:
        """获取向量存储（懒加载）

        Returns:
            向量存储实例
        """
        if self._vector_store is None:
            client = self._get_client()
            embeddings = self._get_embeddings()
            self._vector_store = QdrantVectorStore(
                client=client,
                collection_name=self.COLLECTION_NAME,
                embedding=embeddings,
            )
        return self._vector_store

    def initialize(self) -> None:
        """初始化知识库"""
        if self._initialized:
            self.logger.info("知识库已初始化，跳过")
            return

        self.logger.info("开始初始化知识库...")

        client = self._get_client()

        if not self._should_load_collection(client, self.COLLECTION_NAME):
            self._initialized = True
            self.logger.info("知识库已存在且有数据，跳过加载")
            return

        init_dir = Path(self.config.init_dir)
        if not init_dir.exists():
            self.logger.warning(f"初始化目录不存在：{init_dir}")
            return

        supported_extensions = {".md", ".txt", ".py"}
        files = [
            f
            for f in init_dir.iterdir()
            if f.is_file() and f.suffix.lower() in supported_extensions
        ]

        if not files:
            self.logger.warning(f"初始化目录中没有找到支持的文件：{init_dir}")
            return

        all_documents = []
        for file_path in files:
            self.logger.info(f"加载文件：{file_path.name}")
            docs = self._load_document(file_path)
            if docs:
                all_documents.extend(docs)

        if not all_documents:
            self.logger.warning("没有加载任何文档")
            return

        self.logger.info(f"正在将 {len(all_documents)} 个文档片段加载到 Qdrant...")

        try:
            vector_size = len(self._get_embeddings().embed_query("sample text"))
            client.create_collection(
                collection_name=self.COLLECTION_NAME,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )
        except Exception:
            pass

        vector_store = self._get_vector_store()
        vector_store.add_documents(all_documents)
        self.logger.info(f"知识库初始化完成，共加载 {len(all_documents)} 个文档片段")
        self._initialized = True

    def _should_load_collection(
        self, client: QdrantClient, collection_name: str
    ) -> bool:
        """检查是否需要加载数据

        Args:
            client: Qdrant 客户端
            collection_name: 集合名称

        Returns:
            是否需要加载
        """
        try:
            if not client.collection_exists(collection_name):
                self.logger.info(
                    f"Collection '{collection_name}' 不存在，需要创建并加载数据"
                )
                return True

            collection_info = client.get_collection(collection_name)
            points_count = collection_info.points_count

            if points_count == 0:
                self.logger.info(f"Collection '{collection_name}' 为空，需要加载数据")
                return True

            self.logger.info(
                f"Collection '{collection_name}' 已存在 ({points_count} 条记录)，跳过加载"
            )
            return False
        except Exception as e:
            self.logger.warning(f"检查 Collection 状态失败：{e}，将尝试加载")
            return True

    def _load_document(self, file_path: Path) -> list | None:
        """根据文件类型加载文档

        Args:
            file_path: 文件路径

        Returns:
            文档列表
        """
        suffix = file_path.suffix.lower()

        try:
            if suffix == ".md":
                return self._load_markdown_file(file_path)
            elif suffix == ".txt":
                loader = TextLoader(str(file_path), encoding="utf-8")
            elif suffix == ".py":
                loader = PythonLoader(str(file_path))
            else:
                self.logger.warning(f"不支持的文件类型：{suffix}")
                return None

            return loader.load()
        except Exception as e:
            self.logger.error(f"加载文件失败 {file_path}: {e}")
            return None

    def _load_markdown_file(self, file_path: Path) -> list[Document]:
        """加载 Markdown 文件，使用标题感知切分

        Args:
            file_path: 文件路径

        Returns:
            文档列表
        """
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            self.logger.error(f"读取文件失败 {file_path}: {e}")
            return []

        splitter = MarkdownHeadingSplitter(
            source_file=file_path.name,
            chunk_size=1500,
            chunk_overlap=200,
        )
        documents = splitter.split_text(content)

        if not documents:
            self.logger.warning(f"未能从 {file_path.name} 解析出任何内容")

        return documents

    def search(
        self,
        query: str,
        k: int = 3,
        categories: list[str] | None = None,
        terms: list[str] | None = None,
        source_files: list[str] | None = None,
        **kwargs,
    ) -> list[str]:
        """搜索知识

        Args:
            query: 搜索查询
            k: 返回结果数量
            categories: 可选，按类别过滤
            terms: 可选，按词条名称过滤
            source_files: 可选，按源文件过滤

        Returns:
            搜索结果列表
        """
        try:
            store = self._get_vector_store()

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

                header = f"【来源：{source}"
                if term_name:
                    header += f" | 词条：{term_name}"
                if category:
                    header += f" | 类别：{category}"
                header += "】"

                search_results.append(f"{header}\n{content}\n")

                if len(search_results) >= k:
                    break

            return search_results
        except Exception as e:
            self.logger.error(f"搜索知识库失败：{e}")
            return []

    def save(self, content: str, metadata: dict[str, Any]) -> bool:
        """保存知识

        Args:
            content: 内容
            metadata: 元数据

        Returns:
            是否保存成功
        """
        try:
            doc = Document(
                page_content=content,
                metadata=metadata,
            )

            vector_store = self._get_vector_store()
            vector_store.add_documents([doc])

            self.logger.info(f"知识已保存：{metadata.get('term', 'unknown')}")
            return True

        except Exception as e:
            self.logger.error(f"保存知识失败：{e}")
            return False

    def save_experience(
        self,
        strategy_code: str,
        strategy_name: str,
        design_rationale: str,
        backtest_result: dict,
        reflection: str,
        error_history: list[dict] | None = None,
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
            content = f"""# 策略经验：{strategy_name}

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
**创建时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
"""

            return self.save(
                content,
                {
                    "source_file": "experience.md",
                    "term": strategy_name,
                    "category": "策略经验",
                    "section_level": 1,
                    "experience_type": "strategy",
                    "backtest_metrics": backtest_result.get("metrics", {}),
                },
            )

        except Exception as e:
            self.logger.error(f"保存经验失败：{e}")
            return False

    def search_experience(
        self,
        query: str,
        k: int = 3,
        min_sharpe: float | None = None,
    ) -> list[dict]:
        """搜索历史策略经验

        Args:
            query: 搜索查询
            k: 返回结果数量
            min_sharpe: 最小夏普比率过滤

        Returns:
            经验列表，每条包含 code, rationale, metrics
        """
        try:
            store = self._get_vector_store()
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
            self.logger.error(f"搜索经验失败：{e}")
            return []
