"""TF-IDF 向量化器单元测试"""

import numpy as np

from long_earn.memory.tfidf import TfidfVectorizer, cosine_similarity


class TestTfidfVectorizer:
    def test_fit_transform_shortcut(self):
        docs = ["动量策略选股", "均值回归交易策略", "动量因子分析"]
        vec = TfidfVectorizer(max_features=100)
        result = vec.fit_transform(docs)
        assert result.shape[0] == 3
        assert len(vec.vocabulary_) > 0

    def test_max_features_truncation(self):
        docs = [f"策略 {i}" for i in range(50)]
        vec = TfidfVectorizer(max_features=10)
        vec.fit(docs)
        assert len(vec.vocabulary_) <= 10

    def test_empty_document(self):
        docs = ["", "有效文档内容"]
        vec = TfidfVectorizer()
        vec.fit(docs)
        result = vec.transform([""])
        assert result.shape[1] == len(vec.vocabulary_)


class TestCosineSimilarity:
    def test_multiple_documents(self):
        vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        matrix = np.array(
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.5, 0.5, 0.0]], dtype=np.float32
        )
        sim = cosine_similarity(vec, matrix)
        assert sim.shape == (3,)
        assert sim[0] > sim[1]
