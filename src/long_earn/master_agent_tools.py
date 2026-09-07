"""主智能体工具层（ADR-024 §C 工具分层 / §D 子代理任务）

工具按两组前缀划分，命名即契约：

- ``query_*`` — 只读、秒级、可在 ReAct 循环内高频并行调用；除 ``web_search``
  （联网检索 Provider，ADR-021 审计豁免的基础设施能力）外零语言模型调用；
- ``run_*`` — 有状态、长时（10 秒级以上）、含语言模型推理的子图任务；
  经 :class:`~long_earn.master_agent_tasks.TaskRunner` 提交后台执行并返回
  任务句柄，主循环立即继续；``query_task`` 轮询进度与产物（ADR-024 §D）。

所有工具产出 typed dataclass，经 :meth:`ToolOutput.render` 渲染为
「摘要 + <details> 结构化详情」两段文本，禁止截断式压平。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

import polars as pl
from langchain_core.tools import tool

from long_earn.master_agent_tasks import (
    TASK_FAILED,
    TASK_RUNNING,
    TASK_SUCCEEDED,
    TaskRunner,
)
from long_earn.ontology import ConceptQuery
from long_earn.services.kimi_web_search import kimi_web_search

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext

# run_stock_analysis 摘要段的分析结论前缀长度（渐进披露，详情段保留全文）
_ANALYSIS_HEAD_LIMIT = 150


# ── 结构化输出契约 ──────────────────────────────────────────────


@dataclass
class ToolOutput:
    """工具结构化输出基类（ADR-024 §C）。

    子类字段即结构化详情 schema；``render`` 渲染为两段文本：
    摘要段供主模型快速决策，``<details>`` 段为 JSON 详情（可续查字段）。
    """

    summary: str

    def render(self) -> str:
        payload = {"tool": _tool_key(type(self).__name__), **_detail_fields(self)}
        details = json.dumps(payload, ensure_ascii=False, default=str)
        return f"{self.summary}\n\n<details>\n{details}\n</details>"


@dataclass
class QueryEventsOutput(ToolOutput):
    """query_events 产出：Substance 激活的事件物质文本。"""

    query: str
    items: list[str]


@dataclass
class QueryOntologyOutput(ToolOutput):
    """query_ontology 产出：概念解析结果与图谱关联。"""

    subject: str
    aspect: str
    resolution_kind: str
    data_preview: str
    data: Any
    related_nodes: list[dict[str, str]]
    provenance: list[str]


@dataclass
class QueryMarketOutput(ToolOutput):
    """query_market 产出：标的最新报价快照。"""

    quotes: list[dict[str, Any]]


@dataclass
class QueryMemoryOutput(ToolOutput):
    """query_memory 产出：记忆检索结果。"""

    query: str
    results: list[str]


@dataclass
class WebSearchOutput(ToolOutput):
    """web_search 产出：联网检索结果。"""

    results: list[dict[str, str]]


@dataclass
class RunResearchOutput(ToolOutput):
    """run_research 产出：策略研发结果。"""

    result: str
    strategy_name: str
    strategy_yaml: str
    metrics: dict[str, Any]


@dataclass
class RunStockAnalysisOutput(ToolOutput):
    """run_stock_analysis 产出：多视角分析结论。"""

    analysis: str


@dataclass
class RunEventCollectionOutput(ToolOutput):
    """run_event_collection 产出：事件采集与推理统计。"""

    collected_count: int
    event_count: int
    relation_count: int
    saved_count: int
    events: list[dict[str, Any]]


@dataclass
class TaskHandleOutput(ToolOutput):
    """run_* 提交产出：后台任务句柄（ADR-024 §D）。"""

    task_id: str
    task_kind: str
    status: str


@dataclass
class QueryTaskOutput(ToolOutput):
    """query_task 产出：后台任务进度（完成任务的产物经底层输出原文返回）。"""

    task_id: str
    task_kind: str
    status: str
    error: str
    retryable: bool


def _detail_fields(output: ToolOutput) -> dict[str, Any]:
    data = asdict(output)
    data.pop("summary", None)
    return {k: v for k, v in data.items() if v not in (None, "", [], {})}


def _tool_key(cls_name: str) -> str:
    """输出类名转工具名：``QueryEventsOutput`` → ``query_events``。"""
    stripped = re.sub(r"Output$", "", cls_name)
    return re.sub(r"(?<!^)(?=[A-Z])", "_", stripped).lower()


def _preview_concept_data(data: Any) -> tuple[str, Any]:
    """概念数据渐进披露：大面板与长列表只返回形状与前若干行。"""
    if isinstance(data, pl.DataFrame):
        return f"{data.height} 行 × {data.width} 列（前 5 行）", data.head(5).to_dicts()
    if isinstance(data, list):
        return f"{len(data)} 项（前 5 项）", data[:5]
    return "", data


def _parse_details(text: str) -> dict[str, Any]:
    """解析 ``render`` 输出的 ``<details>`` JSON 段（测试与调试辅助）。"""
    body = text.split("<details>\n", 1)[1].rsplit("\n</details>", 1)[0]
    parsed: dict[str, Any] = json.loads(body)
    return parsed


# ── query_* 工具组 ───────────────────────────────────────────────


def _make_query_events_tool(context: RuntimeContext) -> Any:
    logger = context.logger
    monitoring = context.monitoring
    memory = context.memory

    @tool
    def query_events(query: str, k: int = 5) -> str:
        """事件检索：从 Substance 底座激活与查询相关的已积累事件与影响关系
        （只读、秒级；未激活到相关事件时先运行 run_event_collection 补采集）。

        Args:
            query: 事件查询（标的、主题或新闻关键词，如"茅台 财报"）
            k: 最大返回条数（默认 5）

        Returns:
            摘要 + <details> 结构化详情（items 为激活的事件物质文本）
        """
        with monitoring.track("query_events"):
            logger.info(f"query_events 调用: {query}, k={k}")
            try:
                items = memory.activate_events(query, k=k)
            except Exception as e:
                logger.error(f"query_events 失败: {e}")
                return QueryEventsOutput(
                    summary=f"query_events 执行失败: {e}", query=query, items=[]
                ).render()
            summary = (
                f"激活 {len(items)} 条相关事件物质"
                if items
                else "未激活到相关事件（可先运行 run_event_collection 补采集）"
            )
            return QueryEventsOutput(summary=summary, query=query, items=items).render()

    return query_events


def _make_query_ontology_tool(context: RuntimeContext) -> Any:
    logger = context.logger
    monitoring = context.monitoring

    @tool
    def query_ontology(
        subject: str,
        aspect: str = "",
        time: str = "",
        as_of: str = "",
    ) -> str:
        """本体查询：经 Connector.get_concept 单一入口解析概念并取数（只读、秒级）。
        可查财务指标（roe）、标的（600519.SH）、概念（盈利能力）、universe（csi300）、
        行业成分等，返回概念数据、图谱关联节点与数据溯源。

        Args:
            subject: 查询主题（标的代码、指标名、概念名或股票池名）
            aspect: 数据视角（可选，如指标面板、成分列表）
            time: 时间窗（可选）
            as_of: 时点（PIT 截止日，可选）

        Returns:
            摘要 + <details> 结构化详情（resolution_kind / data / related_nodes）
        """
        with monitoring.track("query_ontology"):
            logger.info(f"query_ontology 调用: {subject} / {aspect}")
            connector = context.connector
            if connector is None:
                return QueryOntologyOutput(
                    summary="本体连接器未初始化，无法执行本体查询",
                    subject=subject,
                    aspect=aspect,
                    resolution_kind="unavailable",
                    data_preview="",
                    data=None,
                    related_nodes=[],
                    provenance=[],
                ).render()
            try:
                result = connector.get_concept(
                    ConceptQuery(subject=subject, aspect=aspect, time=time, as_of=as_of)
                )
            except Exception as e:
                logger.error(f"query_ontology 失败: {e}")
                return QueryOntologyOutput(
                    summary=f"query_ontology 执行失败: {e}",
                    subject=subject,
                    aspect=aspect,
                    resolution_kind="error",
                    data_preview="",
                    data=None,
                    related_nodes=[],
                    provenance=[],
                ).render()
            preview, data = _preview_concept_data(result.data)
            kind = (
                result.resolution.kind if result.resolution is not None else "unknown"
            )
            nodes = [
                {"sid": n.sid, "label": n.label, "domain": str(n.domain)}
                for n in result.related_nodes[:20]
            ]
            summary = f"{subject} 解析为 {kind}"
            if preview:
                summary += f"，数据 {preview}"
            if nodes:
                summary += f"，关联 {len(nodes)} 个图谱节点"
            return QueryOntologyOutput(
                summary=summary,
                subject=subject,
                aspect=aspect,
                resolution_kind=kind,
                data_preview=preview,
                data=data,
                related_nodes=nodes,
                provenance=result.provenance,
            ).render()

    return query_ontology


def _make_query_market_tool(context: RuntimeContext) -> Any:
    logger = context.logger
    monitoring = context.monitoring

    @tool
    def query_market(symbols: str) -> str:
        """实时行情：获取标的最新报价快照（只读、秒级；主源 miniqmt）。

        Args:
            symbols: 逗号分隔的标的代码（如 "600519,000001"，至多 10 个）

        Returns:
            摘要 + <details> 结构化详情（quotes 为各标的报价快照）
        """
        with monitoring.track("query_market"):
            symbol_list = [s.strip() for s in symbols.split(",") if s.strip()][:10]
            if not symbol_list:
                return QueryMarketOutput(
                    summary="未提供有效标的代码", quotes=[]
                ).render()
            logger.info(f"query_market 调用: {symbol_list}")
            provider = context.realtime_provider
            if provider is None or not provider.is_available:
                return QueryMarketOutput(
                    summary="实时行情源不可用（未初始化或主源离线）", quotes=[]
                ).render()
            quotes: list[dict[str, Any]] = []
            for symbol in symbol_list:
                try:
                    quote = provider.get_latest_quote(symbol)
                except Exception as e:
                    logger.warning(f"query_market: {symbol} 行情获取失败: {e}")
                    continue
                quotes.append(_format_quote(symbol, quote))
            summary = f"获取 {len(quotes)}/{len(symbol_list)} 个标的的最新报价"
            return QueryMarketOutput(summary=summary, quotes=quotes).render()

    return query_market


def _format_quote(symbol: str, quote: dict[str, Any]) -> dict[str, Any]:
    """报价快照字段映射与涨跌幅计算。"""
    price = float(quote.get("price", 0.0) or 0.0)
    pre_close = float(quote.get("preClose", 0.0) or 0.0)
    change_pct = round((price - pre_close) / pre_close * 100, 2) if pre_close else None
    return {
        "symbol": symbol,
        "price": price,
        "change_pct": change_pct,
        "open": float(quote.get("open", 0.0) or 0.0),
        "high": float(quote.get("high", 0.0) or 0.0),
        "low": float(quote.get("low", 0.0) or 0.0),
        "pre_close": pre_close,
        "volume": quote.get("volume", 0),
        "time": quote.get("time", ""),
        "source": quote.get("source", ""),
    }


def _make_query_memory_tool(context: RuntimeContext) -> Any:
    logger = context.logger
    monitoring = context.monitoring
    memory = context.memory

    @tool
    def query_memory(query: str, k: int = 3) -> str:
        """记忆检索：从历史策略经验与知识库检索相关内容（只读、秒级）。

        Args:
            query: 检索查询
            k: 返回结果数量（默认 3）

        Returns:
            摘要 + <details> 结构化详情（results 为检索到的记忆文本）
        """
        with monitoring.track("query_memory"):
            logger.info(f"query_memory 调用: {query}, k={k}")
            try:
                results = memory.search(query, k=k)
            except Exception as e:
                logger.error(f"query_memory 失败: {e}")
                return QueryMemoryOutput(
                    summary=f"query_memory 执行失败: {e}",
                    query=query,
                    results=[],
                ).render()
            summary = (
                f"检索到 {len(results)} 条相关记忆" if results else "未检索到相关记忆"
            )
            return QueryMemoryOutput(
                summary=summary, query=query, results=results
            ).render()

    return query_memory


def _make_web_search_tool(context: RuntimeContext) -> Any:
    logger = context.logger
    monitoring = context.monitoring

    @tool
    def web_search(query: str) -> str:
        """网络搜索：使用 Kimi API 进行实时联网检索，获取最新信息
        （只读；外部检索 Provider 基础设施能力）。

        Args:
            query: 搜索关键词

        Returns:
            摘要 + <details> 结构化详情（results 为标题与正文）
        """
        with monitoring.track("web_search"):
            logger.info(f"web_search 调用: {query}")
            try:
                raw_results = kimi_web_search(query)
            except Exception as e:
                logger.error(f"web_search 失败: {e}")
                return WebSearchOutput(
                    summary=f"web_search 执行失败: {e}", results=[]
                ).render()
            results = [
                {"title": r.get("title", ""), "content": r.get("content", "")}
                for r in raw_results
            ]
            summary = (
                "检索到 {} 条结果：{}".format(
                    len(results), "；".join(r["title"] for r in results[:3])
                )
                if results
                else "未找到搜索结果"
            )
            return WebSearchOutput(summary=summary, results=results).render()

    return web_search


# ── run_* 工具组 ────────────────────────────────────────────────


def _make_run_research_tool(
    context: RuntimeContext, research_agent: Any, runner: TaskRunner
) -> Any:
    logger = context.logger
    monitoring = context.monitoring

    @tool
    def run_research(idea: str, constraints: str = "") -> str:
        """策略研发：提交 Think-on-Graph ResearchAgent 后台执行
        （长时任务，分钟级），立即返回任务句柄；用 query_task(task_id)
        轮询进度与结果（最佳策略 YAML、指标与探索路径）。

        Args:
            idea: 策略研发想法或方向描述
            constraints: 约束条件（可选，如股票池、风险偏好、股票分析结论）

        Returns:
            摘要 + <details> 结构化详情（task_id / task_kind / status）
        """
        logger.info(f"run_research 调用: {idea} (约束: {constraints})")

        def _execute() -> RunResearchOutput:
            with monitoring.track("run_research"):
                result = research_agent.invoke(idea, constraints)
            backtest = result.get("backtest_result")
            metrics: dict[str, Any] = {}
            if isinstance(backtest, dict):
                candidate = backtest.get("metrics", backtest)
                if isinstance(candidate, dict):
                    metrics = candidate
            name = result.get("strategy_name", "")
            strategy_yaml = result.get("strategy_yaml") or result.get(
                "optimized_strategy_yaml", ""
            )
            result_text = result.get("result", "")
            lines: list[str] = []
            if result_text:
                lines.append(result_text)
            if name:
                lines.append(f"策略: {name}")
            for key in ("total_return", "sharpe_ratio", "max_drawdown"):
                if key in metrics:
                    lines.append(f"{key}: {metrics[key]}")
            summary = "\n".join(lines) if lines else "策略研发未产出有效结果"
            return RunResearchOutput(
                summary=summary,
                result=result_text,
                strategy_name=name,
                strategy_yaml=strategy_yaml,
                metrics=metrics,
            )

        task_id = runner.submit("run_research", _execute)
        return TaskHandleOutput(
            summary=(
                f"策略研发任务已提交: {task_id}（分钟级长时任务），"
                f'用 query_task(task_id="{task_id}") 轮询结果'
            ),
            task_id=task_id,
            task_kind="run_research",
            status=TASK_RUNNING,
        ).render()

    return run_research


def _make_run_stock_analysis_tool(
    context: RuntimeContext, stock_analysis_subgraph: Any, runner: TaskRunner
) -> Any:
    logger = context.logger
    monitoring = context.monitoring

    @tool
    def run_stock_analysis(query: str, symbols: str = "") -> str:
        """股票分析：提交五视角并行分析子图后台执行
        （巴菲特/芒格/彼得林奇/费雪/资金流向），立即返回任务句柄；用
        query_task(task_id) 轮询进度与综合分析结论（长时任务）。

        Args:
            query: 股票分析查询（如股票名称、代码或分析方向）
            symbols: 特定股票代码（可选，如 600519）

        Returns:
            摘要 + <details> 结构化详情（task_id / task_kind / status）
        """
        full_query = f"{query} (股票: {symbols})" if symbols else query
        logger.info(f"run_stock_analysis 调用: {full_query}")

        def _execute() -> RunStockAnalysisOutput:
            with monitoring.track("run_stock_analysis"):
                result = stock_analysis_subgraph.invoke({"query": full_query})
            analysis = (
                result.get("summary")
                or result.get("error")
                or json.dumps(result, ensure_ascii=False, default=str)
            )
            head = analysis[:_ANALYSIS_HEAD_LIMIT]
            summary = head + ("…" if len(analysis) > _ANALYSIS_HEAD_LIMIT else "")
            return RunStockAnalysisOutput(
                summary=f"股票分析完成：{summary}", analysis=analysis
            )

        task_id = runner.submit("run_stock_analysis", _execute)
        return TaskHandleOutput(
            summary=(
                f"股票分析任务已提交: {task_id}（长时任务），"
                f'用 query_task(task_id="{task_id}") 轮询结果'
            ),
            task_id=task_id,
            task_kind="run_stock_analysis",
            status=TASK_RUNNING,
        ).render()

    return run_stock_analysis


def _make_run_event_collection_tool(
    context: RuntimeContext, event_inference_subgraph: Any, runner: TaskRunner
) -> Any:
    logger = context.logger
    monitoring = context.monitoring

    @tool
    def run_event_collection(query: str) -> str:
        """事件采集与推理：提交事件推理子图后台执行，拉取新闻素材、抽取事件、
        推理市场影响并落库 Substance（长时任务，含语言模型推理），立即返回任务
        句柄；query_events 覆盖不足时先运行本工具，完成后重新 query_events。

        Args:
            query: 采集主题（新闻内容、热点话题、标的或板块关键词）

        Returns:
            摘要 + <details> 结构化详情（task_id / task_kind / status）
        """
        logger.info(f"run_event_collection 调用: {query}")

        def _execute() -> RunEventCollectionOutput:
            with monitoring.track("run_event_collection"):
                result = event_inference_subgraph.invoke({"query": query})
            stats = result.get("summary") or {}
            collected = result.get("collected_items") or []
            extracted = result.get("extracted_events") or []
            relations = result.get("propagated_relations") or []
            saved_sids = result.get("saved_sids") or []
            event_count = int(stats.get("event_count", len(extracted)))
            relation_count = int(stats.get("relation_count", len(relations)))
            events = [
                {
                    key: e.get(key)
                    for key in ("content", "symbols", "sentiment", "category")
                }
                for e in extracted
            ]
            if not collected:
                summary = "未采集到相关素材（0 条），无新事件入库"
            else:
                summary = (
                    f"事件采集完成：素材 {len(collected)} 条 → "
                    f"事件 {event_count} 条、影响关系 {relation_count} 条"
                )
            return RunEventCollectionOutput(
                summary=summary,
                collected_count=len(collected),
                event_count=event_count,
                relation_count=relation_count,
                saved_count=len(saved_sids),
                events=events,
            )

        task_id = runner.submit("run_event_collection", _execute)
        return TaskHandleOutput(
            summary=(
                f"事件采集任务已提交: {task_id}（长时任务），"
                f'用 query_task(task_id="{task_id}") 轮询结果'
            ),
            task_id=task_id,
            task_kind="run_event_collection",
            status=TASK_RUNNING,
        ).render()

    return run_event_collection


def _make_query_task_tool(context: RuntimeContext, runner: TaskRunner) -> Any:
    logger = context.logger
    monitoring = context.monitoring

    @tool
    def query_task(task_id: str) -> str:
        """查询后台任务进度与产物（只读、秒级）：返回运行中/已完成/已失败状态；
        已完成任务返回底层结构化产物（策略 YAML、分析结论、事件清单等），
        失败任务返回失败原因与可重试标记。

        Args:
            task_id: 任务句柄 ID（run_* 工具返回的 task-N）

        Returns:
            运行中/失败：摘要 + <details> 结构化详情；
            已完成：任务状态行 + 底层输出「摘要 + <details>」原文
        """
        with monitoring.track("query_task"):
            logger.info(f"query_task 调用: {task_id}")
            state = runner.get(task_id)
            if state is None:
                return QueryTaskOutput(
                    summary=f"任务不存在: {task_id}（task_id 来自 run_* 工具返回）",
                    task_id=task_id,
                    task_kind="",
                    status="unknown",
                    error="",
                    retryable=False,
                ).render()
            if state.status == TASK_SUCCEEDED:
                output = state.output
                assert output is not None  # 成功态契约保证
                return (
                    f"任务 {task_id}（{state.kind}）已完成，结果如下。\n\n"
                    f"{output.render()}"
                )
            if state.status == TASK_FAILED:
                return QueryTaskOutput(
                    summary=(
                        f"任务 {task_id}（{state.kind}）失败: {state.error}"
                        f"{'（可重新提交 run_*）' if state.retryable else ''}"
                    ),
                    task_id=task_id,
                    task_kind=state.kind,
                    status=TASK_FAILED,
                    error=state.error,
                    retryable=state.retryable,
                ).render()
            return QueryTaskOutput(
                summary=(f"任务 {task_id}（{state.kind}）仍在运行，请稍后再次查询"),
                task_id=task_id,
                task_kind=state.kind,
                status=TASK_RUNNING,
                error="",
                retryable=False,
            ).render()

    return query_task


# ── 工具集组装 ──────────────────────────────────────────────────


def build_master_tools(
    context: RuntimeContext,
    *,
    research_agent: Any,
    stock_analysis_subgraph: Any,
    event_inference_subgraph: Any,
) -> list[Any]:
    """构建 ADR-024 §C 分层工具集（6 个 ``query_*`` + 3 个 ``run_*``）。

    ``run_*`` 任务经共享 :class:`TaskRunner` 后台执行（ADR-024 §D），
    句柄与进度经 ``query_task`` 查询；Runner 生命周期随工具集（进程内）。

    Args:
        context: 运行时上下文（DI 容器；connector / realtime_provider
            由工具闭包在调用期读取，允许为空并结构化降级）
        research_agent: ResearchAgent 实例（run_research 委托对象）
        stock_analysis_subgraph: 股票分析编译子图
        event_inference_subgraph: 事件推理编译子图

    Returns:
        LangChain 工具列表
    """
    runner = TaskRunner(context.logger)
    return [
        _make_query_events_tool(context),
        _make_query_ontology_tool(context),
        _make_query_market_tool(context),
        _make_query_memory_tool(context),
        _make_query_task_tool(context, runner),
        _make_web_search_tool(context),
        _make_run_research_tool(context, research_agent, runner),
        _make_run_stock_analysis_tool(context, stock_analysis_subgraph, runner),
        _make_run_event_collection_tool(context, event_inference_subgraph, runner),
    ]
