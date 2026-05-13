"""TF-IDF 向量化器单元测试"""

import numpy as np
import pytest

from long_earn.memory.tfidf import TfidfVectorizer, cosine_similarity


class TestTfidfVectorizer:
    def test_fit_builds_vocabulary(self):
        docs = ["动量策略选股", "均值回归交易策略", "动量因子分析"]
        vec = TfidfVectorizer(max_features=100)
        vec.fit(docs)
        assert len(vec.vocabulary_) > 0
        assert vec.idf_ is not None

    def test_transform_returns_2d_array(self):
        docs = ["动量策略选股", "均值回归交易策略", "动量因子分析"]
        vec = TfidfVectorizer(max_features=100)
        vec.fit(docs)
        result = vec.transform(docs)
        assert isinstance(result, np.ndarray)
        assert result.ndim == 2
        assert result.shape[0] == 3

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

    def test_transform_before_fit_raises(self):
        vec = TfidfVectorizer()
        with pytest.raises(ValueError, match="先调用 fit"):
            vec.transform(["test"])

    def test_tokenize_handles_mixed_text(self):
        vec = TfidfVectorizer()
        tokens = vec._tokenize("测试test混合text内容")
        assert len(tokens) > 0
        assert any("测试" in t for t in tokens) or any("test" in t for t in tokens)

    def test_single_document_idf(self):
        vec = TfidfVectorizer()
        vec.fit(["唯一文档"])
        assert vec.idf_ is not None
        # 单文档 IDF 应为正值（平滑处理）
        assert np.all(vec.idf_ > 0)


class TestCosineSimilarity:
    def test_perfect_similarity(self):
        vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        matrix = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
        sim = cosine_similarity(vec, matrix)
        assert np.isclose(sim[0], 1.0, atol=1e-4)

    def test_zero_similarity(self):
        vec = np.array([1.0, 0.0], dtype=np.float32)
        matrix = np.array([[0.0, 1.0]], dtype=np.float32)
        sim = cosine_similarity(vec, matrix)
        assert np.isclose(sim[0], 0.0, atol=1e-4)

    def test_zero_query_vector(self):
        vec = np.zeros(3, dtype=np.float32)
        matrix = np.random.randn(5, 3).astype(np.float32)
        sim = cosine_similarity(vec, matrix)
        assert np.all(sim == 0)

    def test_multiple_documents(self):
        vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        matrix = np.array(
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.5, 0.5, 0.0]], dtype=np.float32
        )
        sim = cosine_similarity(vec, matrix)
        assert sim.shape == (3,)
        assert sim[0] > sim[1]
