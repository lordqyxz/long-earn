"""财务字段知识库检索测试。

验证 LLM 通过 MemoryService.search() → SubstanceStore.search() 能从 init/ 知识库
正确提取 20 个财务字段的背景信息（ADR-007 Phase 3 + ADR-014 任务7）。

测试对象：init/01_data.md 的财务字段说明（原 09_financial_fields.md 已合并入此）
测试哲学：验证"大模型能从知识库提取需要的字段信息"这一端到端契约，
不验证检索引擎实现细节。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from long_earn.substance.store import SubstanceStore

# init/ 知识库源文件目录（项目根目录下）
INIT_DIR = Path(__file__).resolve().parents[3] / "init"


@pytest.fixture(scope="module")
def kb_store() -> SubstanceStore:
    """加载 init/ 目录构建知识库（模块级共享，避免重复加载）。"""
    store = SubstanceStore()
    count = store.load_directory(INIT_DIR)
    assert count > 0, f"知识库应加载到物质，init/ 目录: {INIT_DIR}"
    return store


# ── 字段检索契约：每个字段的自然语言查询应能命中背景说明 ──────────


class TestFinancialFieldRetrieval:
    """验证大模型用自然语言查询字段时，知识库能返回相关背景说明。

    模拟 LLM 在策略研发时可能发出的查询，确保关键字段都能被检索到。
    """

    @pytest.mark.parametrize(
        "query,expected_keywords",
        [
            # 利润表字段
            ("ROE 净资产收益率 怎么算", ["净资产收益率", "roe"]),
            ("每股收益 EPS 含义", ["每股收益", "eps"]),
            ("研发费用 研发投入", ["研发费用", "research_expenses"]),
            ("营业收入 revenue", ["营业收入"]),
            # 资产负债表字段
            ("资产负债率 杠杆", ["资产负债率", "debt_to_assets"]),
            ("每股净资产 bps", ["每股净资产", "bps"]),
            # 现金流量表字段
            ("资本支出 capex 自由现金流", ["资本支出", "capex"]),
            ("经营现金流 ocf", ["经营活动现金流", "ocf"]),
            # Pershareindex 预计算字段
            ("加权净资产收益率 roe_weighted", ["加权", "roe_weighted"]),
            ("净利率 盈利能力", ["净利率", "net_profit_margin"]),
            # 衍生指标
            ("毛利率 gross_margin", ["毛利率", "gross_margin"]),
            ("净利润同比增长率", ["净利润同比增长", "net_profit_yoy"]),
        ],
        ids=[
            "roe",
            "eps",
            "research_expenses",
            "revenue",
            "debt_to_assets",
            "bps",
            "capex",
            "ocf",
            "roe_weighted",
            "net_profit_margin",
            "gross_margin",
            "net_profit_yoy",
        ],
    )
    def test_field_query_returns_relevant_background(
        self,
        kb_store: SubstanceStore,
        query: str,
        expected_keywords: list[str],
    ):
        """每个字段的自然语言查询应返回含关键字的结果。"""
        results = kb_store.search(query, k=3)
        assert len(results) > 0, f"查询 '{query}' 未返回任何结果"

        # 至少有一个结果包含期望的关键词
        all_content = " ".join(r["content"].lower() for r in results)
        matched = any(kw.lower() in all_content for kw in expected_keywords)
        assert matched, (
            f"查询 '{query}' 返回结果未包含期望关键词 {expected_keywords}。\n"
            f"返回内容片段: {results[0]['content'][:200]}"
        )


class TestFinancialFieldDetailRetrieval:
    """验证知识库能返回字段的详细背景（计算方法/数据来源/策略提示）。"""

    def test_roe_calculation_method_retrievable(self, kb_store: SubstanceStore):
        """ROE 的计算方法应能被检索到。"""
        results = kb_store.search("ROE 计算公式 净利润 股东权益", k=3)
        assert len(results) > 0
        content = " ".join(r["content"] for r in results)
        # 应包含计算方法的关键要素
        assert "净利润" in content and "股东权益" in content, (
            f"ROE 计算方法未检索到，返回: {content[:300]}"
        )

    def test_weighted_roe_formula_retrievable(self, kb_store: SubstanceStore):
        """加权 ROE 的证监会公式应能被检索到。"""
        results = kb_store.search("加权净资产收益率 证监会 公式", k=3)
        assert len(results) > 0
        content = " ".join(r["content"] for r in results)
        # 加权 ROE 应提及证监会或加权计算
        assert "加权" in content, (
            f"加权 ROE 背景未检索到，返回: {content[:300]}"
        )

    def test_fcf_formula_retrievable(self, kb_store: SubstanceStore):
        """自由现金流 FCF = OCF - capex 应能被检索到。"""
        results = kb_store.search("自由现金流 FCF 公式", k=3)
        assert len(results) > 0
        content = " ".join(r["content"] for r in results)
        assert "OCF" in content or "ocf" in content.lower(), (
            f"FCF 公式未检索到 OCF，返回: {content[:300]}"
        )

    def test_pit_contract_retrievable(self, kb_store: SubstanceStore):
        """PIT 对齐契约说明应能被检索到。"""
        results = kb_store.search("PIT 公告日 未来函数 对齐", k=3)
        assert len(results) > 0
        content = " ".join(r["content"] for r in results)
        assert "公告" in content or "announce" in content.lower(), (
            f"PIT 契约说明未检索到，返回: {content[:300]}"
        )


class TestKnowledgeBaseFieldCoverage:
    """验证知识库文档覆盖全部 20 个财务字段。

    检索质量（大模型能否用自然语言查到）已由 TestFinancialFieldRetrieval 的
    12 个参数化用例覆盖；本类校验源文档本身的字段覆盖完整性。
    """

    def test_field_count_in_data_doc(self):
        """init/01_data.md 财务字段表应包含 20 个字段。"""
        data_doc = INIT_DIR / "01_data.md"
        content = data_doc.read_text(encoding="utf-8")
        from long_earn.backtest.data.miniqmt_provider import FINANCIAL_FIELD_MAP

        documented = sum(
            1 for f in FINANCIAL_FIELD_MAP if f in content
        )
        assert documented == 20, (
            f"01_data.md 应记录全部 20 个字段，实际 {documented} 个"
        )
