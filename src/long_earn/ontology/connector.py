"""本体论连接器 — ADR-014 阶段 C（阶段 F 升级）。

``Connector.get_concept(ConceptQuery)`` 是上层唯一的数据访问入口。上层用"概念"
取数（如 "贵州茅台的盈利能力指标"/"沪深300成分股"/"相关事件"），连接器内部：
1. 解析 subject（名称 → symbol / universe → 成分股）
2. 解析 aspect（概念 → 字段集 / universe / 图谱查询 / 情报方法）
3. 按 resolution 类型分发取数
4. 图谱关联节点（供 LLM 推理增强）
5. 降级链（已废止：ADR-018 改为显式点名源，失败即失败）
6. PIT 裁剪

依赖方向：``ontology`` 不依赖 ``backtest.data`` / ``services``（import-linter 契约）。
连接器通过 ``ConnectorDataProvider`` Protocol（结构化子类型）定义它需要的接口，
由 ``context_init`` 注入具体实现（``MiniQmtDataProvider`` /
``CompositeDataConnector`` 等，均实现 :class:`DataConnector`）。

ADR-014 阶段 F：``ConnectorDataProvider`` 升级为扩展 Protocol，新增
``get_industry_index_panel`` / ``get_industry_constituents`` /
``get_sector_classifications`` / ``get_trading_dates`` /
``get_instrument_detail`` / ``get_full_tick`` 6 个方法签名，覆盖 miniqmt
全数据能力。具体实现（如 ``MiniQmtDataProvider`` / ``CompositeDataConnector``）
已实现这些方法，本 Protocol 仅声明连接器消费的子集。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from typing import Any, Protocol

import pandas as pd
import polars as pl
from loguru import logger

from long_earn.ontology.concept_resolver import (
    ConceptResolution,
    ConceptResolver,
)
from long_earn.ontology.graph import GraphPath, OntologyGraph
from long_earn.ontology.model import (
    OntologyDomain,
    OntologyNode,
    RelationType,
)
from long_earn.ontology.registry import OntologyRegistry

# 季度时间格式 "YYYYQQ" 的长度（如 "2024Q3"）
_QUARTER_TIME_LEN = 6

# ── 连接器需要的数据访问接口（Protocol，不依赖 backtest.data）──────────


class ConnectorDataProvider(Protocol):
    """连接器需要的数据访问接口 — 由具体 DataConnector 实现。

    设计为结构化子类型（Protocol），``ontology`` 不 import ``backtest.data``，
    由 ``context_init`` 注入 ``MiniQmtDataProvider`` / ``CompositeDataConnector``。

    ADR-014 阶段 F：与 :class:`backtest.data.connector.DataConnector` 对齐，
    声明连接器消费的方法子集（含行业指数/行业成分股/板块树/交易日历/标的
    基础信息/实时快照 6 类新能力）。
    """

    def get_financial_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame: ...

    def get_symbols(self, universe_type: str, date: str = "") -> list[str]: ...

    def get_industry_index_panel(
        self,
        industry: str,
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame: ...

    def get_industry_constituents(self, industry: str) -> list[str]: ...

    def get_sector_classifications(self) -> list[str]: ...

    def get_trading_dates(
        self,
        start_date: str = "",
        end_date: str = "",
        market: str = "SSE",
    ) -> list[str]: ...

    def get_instrument_detail(self, stock_code: str) -> dict[str, Any]: ...

    def get_full_tick(self, code_list: list[str]) -> dict[str, Any]: ...


class ConnectorMemoryProvider(Protocol):
    """连接器需要的记忆访问接口 — 由 MemoryServiceImpl 实现。"""

    def search_experience(
        self,
        query: str,
        k: int = 3,
        **kwargs: Any,
    ) -> list[dict[str, Any]]: ...

    def activate_events(
        self,
        query: str,
        k: int = 5,
        **kwargs: Any,
    ) -> list[dict[str, Any]]: ...


def _experience_to_dict(exp: Any) -> dict[str, Any] | Any:
    """把 StrategyExperience dataclass 转为 dict（HTR 调用方用 .get()）。"""
    if is_dataclass(exp):
        return asdict(exp)
    return exp


# ── 概念查询与结果 ──────────────────────────────────────────────────────


@dataclass
class ConceptQuery:
    """概念查询 — 上层唯一的数据访问形式。

    Attributes:
        subject: 主体标识（xt_symbol "600519.SH" / universe "csi300" / 名称 "贵州茅台"）
        aspect: 概念（"盈利能力" / "成分股" / "相关事件" / "动量族经验"）
        time: 时间点/区间（"2024Q3" / "2024-01-01~2024-12-31" / "latest" / ""）
        as_of: PIT 裁剪时刻（回测当日，"" 表示不过滤）
        constraints: 额外约束（min_sharpe / universe / strategy_family 等）
    """

    subject: str
    aspect: str
    time: str = ""
    as_of: str = ""
    constraints: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConceptResult:
    """概念查询结果 — 结构化、可溯源。

    ``data`` 形态由 ``ConceptResolution.kind`` 决定：
    - indicator_panel: polars DataFrame（合并面板）
    - universe: list[str]（成分股代码）
    - event_graph: list[dict]（事件 + 路径）
    - experience: list[dict]（策略经验）
    - intelligence: dict / DataFrame（情报数据）
    """

    concept: str
    subject: str
    data: pl.DataFrame | dict[str, Any] | list[Any]
    provenance: list[str] = field(default_factory=list)
    related_nodes: list[OntologyNode] = field(default_factory=list)
    paths: list[GraphPath] = field(default_factory=list)
    resolution: ConceptResolution | None = None


# ── 连接器 ──────────────────────────────────────────────────────────────


class Connector:
    """本体论连接器 — 单一概念查询入口。

    用法：
        registry = OntologyRegistry(); registry.seed()
        connector = Connector(registry, data_provider=miniqt_provider)
        result = connector.get_concept(ConceptQuery(
            subject="600519.SH", aspect="盈利能力", time="2024Q3", as_of="2024-12-31",
        ))
        # result.data → polars DataFrame 含 roe/gross_margin/...
        # result.related_nodes → 图谱关联指标节点（供 LLM 推理）
    """

    def __init__(
        self,
        registry: OntologyRegistry,
        data_provider: ConnectorDataProvider | None = None,
        memory_provider: ConnectorMemoryProvider | None = None,
    ) -> None:
        self._registry = registry
        self._resolver = ConceptResolver(registry)
        self._data = data_provider
        self._memory = memory_provider

    @property
    def graph(self) -> OntologyGraph:
        return self._registry.graph

    def get_concept(self, query: ConceptQuery) -> ConceptResult:
        """单一入口：解析概念 → 分发取数 → 图谱关联 → 返回结构化结果。"""
        resolution = self._resolver.resolve(query.aspect)
        subject_sid, symbols = self._resolver.resolve_subject(query.subject)
        data, provenance = self._dispatch_fetch(query, resolution, subject_sid, symbols)

        # 图谱关联节点（供 LLM 推理增强）
        related_nodes: list[OntologyNode] = []
        if subject_sid:
            related_domains = resolution.related_domains
            domain_set = None
            if related_domains:
                domain_set = {OntologyDomain(d) for d in related_domains}
            paths = self.graph.traverse(
                subject_sid,
                max_depth=2,
                min_weight=0.0,
                domain_filter=domain_set,
            )
            for p in paths:
                if p.node is not None:
                    related_nodes.append(p.node)
        else:
            paths = []

        return ConceptResult(
            concept=query.aspect,
            subject=query.subject,
            data=data,
            provenance=provenance,
            related_nodes=related_nodes,
            paths=paths,
            resolution=resolution,
        )

    def _dispatch_fetch(
        self,
        query: ConceptQuery,
        resolution: ConceptResolution,
        subject_sid: str,
        symbols: list[str],
    ) -> tuple[pl.DataFrame | dict[str, Any] | list[Any], list[str]]:
        """按 resolution.kind 分发到具体 _fetch_* 方法。

        用闭包字典替代多分支 if/elif，避免圈复杂度超标。
        未识别 kind 返回 ``{"error": ...}``。
        """
        # 闭包字典：kind → () → (data, provenance)
        # 注意每个 lambda 必须返回 tuple，且延迟调用避免无谓执行
        dispatch: dict[
            str,
            Callable[[], tuple[pl.DataFrame | dict[str, Any] | list[Any], list[str]]],
        ] = {
            "indicator_panel": lambda: self._fetch_indicator_panel(
                symbols, resolution, query
            ),
            "universe": lambda: self._fetch_universe(query.subject, query.as_of),
            "event_graph": lambda: self._fetch_event_graph(subject_sid, query),
            "experience": lambda: self._fetch_experience(
                subject_sid, resolution, query
            ),
            "intelligence": lambda: self._fetch_intelligence(
                symbols, resolution, query
            ),
            "industry_panel": lambda: self._fetch_industry_panel(query),
            "industry_constituents": lambda: self._fetch_industry_constituents(query),
            "sector_classifications": self._fetch_sector_classifications,
            "trading_dates": lambda: self._fetch_trading_dates(query),
            "instrument_detail": lambda: self._fetch_instrument_detail(query),
            "realtime_tick": lambda: self._fetch_realtime_tick(query, symbols),
        }
        handler = dispatch.get(resolution.kind)
        if handler is not None:
            return handler()
        return {"error": f"未识别的概念: {query.aspect}"}, ["unknown"]

    # ── 私有：各概念类型的取数实现 ─────────────────────────────────────

    def _fetch_indicator_panel(
        self,
        symbols: list[str],
        resolution: ConceptResolution,
        query: ConceptQuery,
    ) -> tuple[pl.DataFrame, list[str]]:
        """财务指标面板 — 委托 DataProvider.get_financial_panel + 转 polars。"""
        if not symbols or self._data is None:
            return pl.DataFrame(), []
        fields = resolution.payload.get("fields", [])
        field_list = [str(f) for f in fields] if fields else None
        start, end = self._parse_time(query.time)
        df = self._data.get_financial_panel(
            symbols,
            start,
            end,
            field_list,
        )
        if df is None or df.empty:
            return pl.DataFrame(), []
        # pandas MultiIndex → polars
        df = df.reset_index()
        if "date" in df.columns:
            df = df.rename(columns={"date": "timestamp"})
        panel = pl.from_pandas(df)
        return panel, ["data_provider:financial_panel"]

    def _fetch_universe(self, subject: str, as_of: str) -> tuple[list[str], list[str]]:
        """成分股列表 — 委托 DataProvider.get_symbols。"""
        if self._data is None:
            return [], []
        # subject 可能是 universe 概念名或 universe_type
        # 解析器已尝试匹配本体节点；这里用 subject 当 universe_type
        # 尝试本体节点解析
        node = self._registry.find_by_label_or_alias(subject)
        universe_type = ""
        if node is not None and node.properties.get("kind") == "universe":
            universe_type = str(node.properties.get("universe_type", ""))
        if not universe_type:
            # 直接当 universe_type 用（如 "csi300"）
            universe_type = subject
        try:
            symbols = self._data.get_symbols(universe_type, as_of)
            return symbols, [f"data_provider:get_symbols:{universe_type}"]
        except Exception as e:
            logger.warning(f"Connector 取 universe {universe_type} 失败: {e}")
            return [], []

    def _fetch_event_graph(
        self,
        subject_sid: str,
        query: ConceptQuery,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """事件图谱 — 图遍历返回影响该 entity 的事件 + 传导链。"""
        if not subject_sid:
            return [], []
        when = self._parse_as_of(query.as_of)
        paths = self.graph.traverse(
            subject_sid,
            max_depth=3,
            min_weight=0.0,
            relation_types={RelationType.IMPACTS, RelationType.PROPAGATES_TO},
            direction="reverse",
            visible_at=when,
        )
        events: list[dict[str, Any]] = []
        for p in paths:
            events.append(
                {
                    "event_sid": p.sid,
                    "path": p.path,
                    "weight": p.weight,
                    "distance": p.distance,
                }
            )
        return events, ["ontology:graph_traverse:reverse_impacts"]

    def _fetch_experience(
        self,
        subject_sid: str,  # noqa: ARG002 保留供未来图遍历经验节点用
        resolution: ConceptResolution,
        query: ConceptQuery,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """策略经验图谱 — 按因子族检索相似经验。

        ADR-014 任务5：用候选查询词列表替代单一 ``"策略族:{family}"`` 查询。
        jieba 对 ``"策略族:动量"`` 这种"前缀:英文"分词命中率低（之前返回 0 条），
        改为依次尝试多个候选查询词，取第一个有结果的。
        """
        if self._memory is None:
            return [], []
        family = resolution.payload.get("strategy_family", "")
        # 候选查询词：从具体到通用，覆盖 jieba 分词友好的中文表述
        candidate_queries = [
            family,
            f"{family}策略",
            f"{family}因子",
            "动量策略",
            "策略经验",
            "策略优化",
        ]
        # 去重保序
        seen: set[str] = set()
        queries = [
            q for q in candidate_queries if q and q not in seen and not seen.add(q)
        ]
        k = int(query.constraints.get("k", 3))
        provenance = [f"memory:search_experience:{family}"]
        try:
            for q in queries:
                experiences = self._memory.search_experience(query=q, k=k)
                if experiences:
                    # StrategyExperience dataclass → dict（HTR 调用方用 .get()）
                    # 同时把 metrics.sharpe_ratio 提到顶层 sharpe，方便 HTR 调用方
                    result: list[dict[str, Any]] = []
                    for e in experiences:
                        d = _experience_to_dict(e)
                        if isinstance(d, dict):
                            metrics = d.get("metrics", {}) or {}
                            d["sharpe"] = metrics.get(
                                "sharpe_ratio", metrics.get("sharpe", "?")
                            )
                        result.append(d)
                    return result, provenance
        except Exception as e:
            logger.warning(f"Connector 取经验 {family} 失败: {e}")
        return [], provenance

    def _fetch_intelligence(
        self,
        symbols: list[str],
        resolution: ConceptResolution,
        query: ConceptQuery,  # noqa: ARG002 占位方法，query 暂未使用
    ) -> tuple[dict[str, Any], list[str]]:
        """市场情报 — 暂返回占位（情报接口由 MarketIntelligenceProvider 提供，
        阶段 C 暂不接入具体实现，留待后续按需扩展）。"""
        methods = resolution.payload.get("methods", [])
        return {
            "status": "not_implemented",
            "methods": methods,
            "symbols": symbols,
        }, ["intelligence:pending"]

    # ── 私有：miniqmt 全能力取数（ADR-014 阶段 F）─────────────────────

    def _fetch_industry_panel(
        self,
        query: ConceptQuery,
    ) -> tuple[pl.DataFrame, list[str]]:
        """行业指数 K 线面板 — 委托 DataConnector.get_industry_index_panel。

        ``query.subject`` 直接作为行业名/行业指数代码传给底层
        （如 ``"银行"`` / ``"电子"`` / 板块代码）。``query.time`` 解析为
        ``(start_date, end_date)``，``query.constraints.fields`` 作为字段过滤。
        """
        if self._data is None or not query.subject:
            return pl.DataFrame(), []
        start, end = self._parse_time(query.time)
        fields_raw = query.constraints.get("fields")
        fields: list[str] | None = None
        if isinstance(fields_raw, list) and fields_raw:
            fields = [str(f) for f in fields_raw]
        try:
            df = self._data.get_industry_index_panel(
                query.subject,
                start,
                end,
                fields,
            )
        except Exception as e:
            logger.warning(f"Connector 取行业指数 {query.subject} 失败: {e}")
            return pl.DataFrame(), []
        if df is None or df.empty:
            return pl.DataFrame(), []
        df = df.reset_index()
        if "date" in df.columns:
            df = df.rename(columns={"date": "timestamp"})
        panel = pl.from_pandas(df)
        return panel, [f"data_connector:industry_index_panel:{query.subject}"]

    def _fetch_industry_constituents(
        self,
        query: ConceptQuery,
    ) -> tuple[list[str], list[str]]:
        """行业成分股列表 — 委托 DataConnector.get_industry_constituents。"""
        if self._data is None or not query.subject:
            return [], []
        try:
            symbols = self._data.get_industry_constituents(query.subject)
            return symbols, [f"data_connector:industry_constituents:{query.subject}"]
        except Exception as e:
            logger.warning(f"Connector 取行业成分股 {query.subject} 失败: {e}")
            return [], []

    def _fetch_sector_classifications(
        self,
    ) -> tuple[list[str], list[str]]:
        """板块分类树 — 委托 DataConnector.get_sector_classifications。"""
        if self._data is None:
            return [], []
        try:
            sectors = self._data.get_sector_classifications()
            return sectors, ["data_connector:sector_classifications"]
        except Exception as e:
            logger.warning(f"Connector 取板块分类失败: {e}")
            return [], []

    def _fetch_trading_dates(
        self,
        query: ConceptQuery,
    ) -> tuple[list[str], list[str]]:
        """交易日历 — 委托 DataConnector.get_trading_dates。

        ``query.time`` 解析为 ``(start_date, end_date)``，
        ``query.constraints.market`` 覆盖默认市场（默认 ``"SSE"``）。
        """
        if self._data is None:
            return [], []
        start, end = self._parse_time(query.time)
        market = str(query.constraints.get("market", "SSE"))
        try:
            dates = self._data.get_trading_dates(start, end, market)
            return dates, [f"data_connector:trading_dates:{market}"]
        except Exception as e:
            logger.warning(f"Connector 取交易日历失败: {e}")
            return [], []

    def _fetch_instrument_detail(
        self,
        query: ConceptQuery,
    ) -> tuple[dict[str, Any], list[str]]:
        """标的基础信息 — 委托 DataConnector.get_instrument_detail。

        ``query.subject`` 直接作为 ``stock_code`` 传入。支持逗号分隔的
        多标的，此时返回 ``{"instruments": [detail, ...]}``。
        """
        if self._data is None or not query.subject:
            return {}, []
        codes = [s.strip() for s in query.subject.split(",") if s.strip()]
        if not codes:
            return {}, []
        try:
            if len(codes) == 1:
                detail = self._data.get_instrument_detail(codes[0])
                return (
                    detail if isinstance(detail, dict) else {"data": detail},
                    [f"data_connector:instrument_detail:{codes[0]}"],
                )
            # 多标的：逐个查询，聚合返回
            details: list[dict[str, Any]] = []
            for code in codes:
                d = self._data.get_instrument_detail(code)
                if isinstance(d, dict):
                    details.append(d)
            return (
                {"instruments": details},
                [f"data_connector:instrument_detail:{','.join(codes)}"],
            )
        except Exception as e:
            logger.warning(f"Connector 取标的基础信息 {query.subject} 失败: {e}")
            return {}, []

    def _fetch_realtime_tick(
        self,
        query: ConceptQuery,
        symbols: list[str],
    ) -> tuple[dict[str, Any], list[str]]:
        """实时快照 — 委托 DataConnector.get_full_tick。

        优先用 ``resolve_subject`` 解析得到的 ``symbols``（如
        ``"600519.SH,000001.SZ"`` → ``["600519.SH","000001.SZ"]``）；
        若解析失败，回退使用 ``query.subject``（去除空白后）。
        """
        if self._data is None:
            return {}, []
        code_list = symbols or [
            s.strip() for s in query.subject.split(",") if s.strip()
        ]
        if not code_list:
            return {}, []
        try:
            tick = self._data.get_full_tick(code_list)
            if not isinstance(tick, dict):
                return {"data": tick}, [
                    f"data_connector:realtime_tick:{len(code_list)}"
                ]
            return tick, [f"data_connector:realtime_tick:{','.join(code_list)}"]
        except Exception as e:
            logger.warning(f"Connector 取实时快照失败: {e}")
            return {}, []

    # ── 私有：时间解析 ──────────────────────────────────────────────────

    @staticmethod
    def _parse_quarter(q: str) -> tuple[str, str]:
        """解析单季度字符串为 (start_date, end_date)。

        - "2024Q3" → ("2024-07-01", "2024-09-30")
        - 非法格式 → ("", "")
        """
        if "Q" not in q or len(q) != _QUARTER_TIME_LEN:
            return "", ""
        year = int(q[:4])
        quarter = int(q[5])
        quarter_ranges = {
            1: (f"{year}-01-01", f"{year}-03-31"),
            2: (f"{year}-04-01", f"{year}-06-30"),
            3: (f"{year}-07-01", f"{year}-09-30"),
            4: (f"{year}-10-01", f"{year}-12-31"),
        }
        return quarter_ranges.get(quarter, ("", ""))

    @classmethod
    def _parse_time(cls, time: str) -> tuple[str, str]:
        """解析 time 字段为 (start_date, end_date)。

        - "2024Q3" → ("2024-07-01", "2024-09-30")
        - "2024Q1~2024Q4" → ("2024-01-01", "2024-12-31")（各端独立季度解析）
        - "2024-01-01~2024-12-31" → 原样
        - "latest" / "" → 空字符串（让 provider 用默认）
        """
        if not time or time == "latest":
            return "", ""
        if "~" in time:
            parts = [p.strip() for p in time.split("~")]
            start_q = cls._parse_quarter(parts[0])
            end_q = cls._parse_quarter(parts[1])
            # 端点为季度时取该季首日/末日；否则按普通日期原样返回
            return (
                start_q[0] if start_q[0] else parts[0],
                end_q[1] if end_q[1] else parts[1],
            )
        return cls._parse_quarter(time)

    @staticmethod
    def _parse_as_of(as_of: str) -> datetime | None:
        """解析 as_of 为 datetime（PIT 裁剪时刻）。"""
        if not as_of:
            return None
        try:
            return datetime.fromisoformat(as_of)
        except ValueError:
            return None
