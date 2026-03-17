import re
from typing import List
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


class MarkdownHeadingSplitter:
    """Markdown 标题感知切分器

    按标题层级分割文档，保持语义完整性。

    切分策略:
    - h1: 文档标题
    - h2: 一级类别 (如 "一、基础指标类")
    - h3: 二级类别 (如 "1.1 价格指标")
    - h4: 词条标题 (如 "收盘价")
    - 如果单个章节超过 chunk_size，使用 RecursiveCharacterTextSplitter 进一步分割

    Example:
        >>> splitter = MarkdownHeadingSplitter(source_file="example.md")
        >>> docs = splitter.split_text(markdown_content)
    """

    def __init__(
        self,
        source_file: str = "",
        chunk_size: int = 1500,
        chunk_overlap: int = 200,
    ):
        self.source_file = source_file
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.length_function = len
        self.heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$")

    def split_text(self, text: str) -> List[Document]:
        """分割文本为 Document 列表

        Args:
            text: Markdown 文本内容

        Returns:
            Document 列表，每个词条一个 Document
        """
        documents = []
        lines = text.split("\n")

        current_h1 = ""
        current_h2 = ""
        current_h3 = ""
        current_content = []

        def flush_term():
            nonlocal current_h1, current_h2, current_h3, current_content

            if not current_content:
                return

            term_title = (
                current_h3 if current_h3 else (current_h2 if current_h2 else "")
            )
            term_content = "\n".join(current_content).strip()

            if not term_title or not term_content:
                current_content = []
                return

            category = current_h2 if current_h2 else current_h1
            term_name = (
                term_title.split("(")[0].strip() if "(" in term_title else term_title
            )

            doc = Document(
                page_content=f"{term_title}\n\n{term_content}",
                metadata={
                    "source_file": self.source_file,
                    "term": term_name,
                    "category": category,
                    "section_level": 4 if current_h3 else 3,
                },
            )
            documents.append(doc)
            current_content = []

        for line in lines:
            match = self.heading_pattern.match(line)
            if match:
                level = len(match.group(1))
                title = match.group(2).strip()

                if level == 1:
                    current_h1 = title
                    current_h2 = ""
                    current_h3 = ""
                    current_content = []
                elif level == 2:
                    flush_term()
                    current_h2 = title
                    current_h3 = ""
                elif level == 3:
                    flush_term()
                    current_h3 = title
                elif level == 4:
                    flush_term()
                    current_h3 = title
                else:
                    current_content.append(line)
            else:
                current_content.append(line)

        flush_term()

        if not documents:
            doc = Document(
                page_content=text[: self.chunk_size],
                metadata={
                    "source_file": self.source_file,
                    "term": "",
                    "category": "",
                    "section_level": 0,
                },
            )
            documents.append(doc)

        final_docs = []
        for doc in documents:
            if self.length_function(doc.page_content) > self.chunk_size:
                sub_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap,
                    separators=["\n## ", "\n### ", "\n\n", "\n", "。", "！", "？"],
                )
                sub_texts = sub_splitter.split_text(doc.page_content)
                for i, sub_text in enumerate(sub_texts):
                    sub_doc = Document(
                        page_content=sub_text,
                        metadata={
                            **doc.metadata,
                            "chunk_index": i,
                        },
                    )
                    final_docs.append(sub_doc)
            else:
                final_docs.append(doc)

        return final_docs
