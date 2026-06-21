"""轻量级 TF-IDF 向量化器

纯 numpy 实现，无外部依赖，用于文本相似度检索。
"""

import re

import numpy as np


class TfidfVectorizer:
    """TF-IDF 向量化器 — 将文本集合转换为 TF-IDF 矩阵"""

    def __init__(self, max_features: int = 5000):
        self.max_features = max_features
        self.vocabulary_: dict[str, int] = {}
        self.idf_: np.ndarray | None = None

    def _tokenize(self, text: str) -> list[str]:
        """简单中文+英文分词"""

        # 匹配中文字符序列或英文单词
        tokens = re.findall(r"[一-鿿]+|[a-zA-Z0-9_]{2,}", text.lower())
        return tokens

    def fit(self, documents: list[str]) -> "TfidfVectorizer":
        """构建词汇表和 IDF"""
        doc_count = len(documents)
        df: dict[str, float] = {}

        for doc in documents:
            seen = set(self._tokenize(doc))
            for token in seen:
                df[token] = df.get(token, 0) + 1

        # 按文档频率排序，取 top max_features
        sorted_terms = sorted(df.items(), key=lambda x: x[1], reverse=True)
        sorted_terms = sorted_terms[: self.max_features]

        self.vocabulary_ = {term: idx for idx, (term, _) in enumerate(sorted_terms)}
        n_terms = len(self.vocabulary_)

        # 计算 IDF: log((N+1) / (df+1)) + 1 (平滑)
        idf = np.ones(n_terms)
        for term, idx in self.vocabulary_.items():
            idf[idx] = np.log((doc_count + 1) / (df[term] + 1)) + 1.0
        self.idf_ = idf

        return self

    def transform(self, documents: list[str]) -> np.ndarray:
        """将文档转换为 TF-IDF 矩阵"""
        if self.idf_ is None:
            raise ValueError("请先调用 fit() 构建词汇表")

        n_terms = len(self.vocabulary_)
        matrix = np.zeros((len(documents), n_terms), dtype=np.float32)

        for i, doc in enumerate(documents):
            tokens = self._tokenize(doc)
            if not tokens:
                continue
            # TF 计数
            tf = np.zeros(n_terms)
            for token in tokens:
                if token in self.vocabulary_:
                    tf[self.vocabulary_[token]] += 1
            # L2 归一化 TF
            norm = np.linalg.norm(tf)
            if norm > 0:
                tf /= norm
            matrix[i] = tf * self.idf_

        return matrix

    def fit_transform(self, documents: list[str]) -> np.ndarray:
        """构建词汇表并转换"""
        return self.fit(documents).transform(documents)


def cosine_similarity(query_vec: np.ndarray, doc_matrix: np.ndarray) -> np.ndarray:
    """计算查询向量与文档矩阵的余弦相似度

    Args:
        query_vec: 查询向量 (n_features,)
        doc_matrix: 文档矩阵 (n_docs, n_features)

    Returns:
        相似度数组 (n_docs,)
    """
    query_norm = np.linalg.norm(query_vec)
    if query_norm == 0:
        return np.zeros(doc_matrix.shape[0])
    query_vec = query_vec / query_norm
    doc_norms = np.linalg.norm(doc_matrix, axis=1)
    doc_norms[doc_norms == 0] = 1.0
    doc_matrix_norm = doc_matrix / doc_norms[:, np.newaxis]
    return doc_matrix_norm @ query_vec
