"""会话上下文引擎（ADR-024 §B）

上下文工程三机制中本模块承载前两者：

- **结构化笔记**：会话工作集以键值笔记（任务状态、已确认事实、标的清单、
  用户约束）维护，每轮注入；首轮笔记来自 ``prepare_context`` 确定性激活
  结果 ``ContextActivation``（ADR-021），miss 补采集经 ``run_event_collection``
  显式触发（由主模型决策，本模块不做采集）。
- **紧凑化（compaction）**：复用官方 ``SummarizationMiddleware``，消息历史
  超过条数阈值时旧消息折叠为摘要块（在 ``master_agent.py`` 组装）。

按需加载（第三机制）由 §C 的 ``query_*`` 工具分层落地，本模块不涉及。

笔记注入为临时性（``wrap_model_call`` 内 ``request.override`` 修改
system_message），不写入 checkpointer 历史；笔记本体随 thread 生命周期
存续于进程内注册表，``close_session`` 时随摘要沉淀一并清理。
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.config import get_config

from long_earn.core.prompt_loader import MarkdownPromptTemplate

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.runnables import RunnableConfig

    from long_earn.services import LoggerService

# 笔记注入块的标签边界（middleware 注入与测试断言共用）
_NOTES_OPEN_TAG = "<session_notes>"
_NOTES_CLOSE_TAG = "</session_notes>"

# 笔记 JSON 解析：剥离 ```json 围栏
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")

# 工具消息进入笔记提炼 transcript 时保留的摘要行数（render 首段即摘要）
_TOOL_SUMMARY_LINES = 2


@dataclass
class SessionNotes:
    """会话结构化笔记（ADR-024 §B：介于压平历史与全文重读之间的工作集）。"""

    task: str = ""
    facts: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)

    def render(self) -> str:
        """渲染为注入 prompt 的 markdown 块；全空字段跳过。"""
        sections: list[str] = []
        if self.task:
            sections.append(f"## 当前任务\n{self.task}")
        if self.facts:
            body = "\n".join(f"- {item}" for item in self.facts)
            sections.append(f"## 已确认事实\n{body}")
        if self.symbols:
            body = "\n".join(f"- {item}" for item in self.symbols)
            sections.append(f"## 关注标的\n{body}")
        if self.constraints:
            body = "\n".join(f"- {item}" for item in self.constraints)
            sections.append(f"## 用户约束\n{body}")
        if not sections:
            return ""
        inner = "\n\n".join(sections)
        return f"{_NOTES_OPEN_TAG}\n{inner}\n{_NOTES_CLOSE_TAG}"

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class ContextEngine:
    """上下文引擎 — 结构化笔记的维护者（ADR-024 §B）。

    职责：笔记注册表（thread 生命周期）、首轮确定性初始化
    （``ContextActivation``，ADR-021）、轮末 LLM 增量提炼。
    LLM 提炼失败时保留旧笔记，不中断主循环。

    Args:
        llm: 已构造的聊天模型（笔记提炼复用主智能体模型，禁止自取）。
        logger: 日志服务。
        prepare_context: ADR-021 确定性激活入口（``RuntimeContext.prepare_context``）。
    """

    def __init__(
        self,
        llm: BaseChatModel,
        logger: LoggerService,
        prepare_context: Callable[[str], str],
    ) -> None:
        self._llm = llm
        self._logger = logger
        self._prepare_context = prepare_context
        self._lock = threading.Lock()
        self._notes: dict[str, SessionNotes] = {}

    def has_notes(self, thread_id: str) -> bool:
        with self._lock:
            return thread_id in self._notes

    def get_notes(self, thread_id: str) -> SessionNotes | None:
        with self._lock:
            notes = self._notes.get(thread_id)
            return SessionNotes(**asdict(notes)) if notes else None

    def initialize_notes(self, thread_id: str, query: str) -> SessionNotes:
        """首轮笔记初始化 — ``ContextActivation`` 确定性激活（ADR-021/§B）。

        激活 hit 时事件文本进入 facts；miss 或异常时 facts 留空，
        事件补采集交由主模型经 ``run_event_collection`` 显式决策。
        """
        facts: list[str] = []
        try:
            raw = self._prepare_context(query)
            text = raw if isinstance(raw, str) else ""
        except Exception as e:
            self._logger.warning(f"上下文激活失败，笔记以空事实初始化: {e}")
            text = ""
        if text:
            facts.extend(line for line in text.splitlines() if line.strip())

        notes = SessionNotes(task=query, facts=facts)
        with self._lock:
            self._notes[thread_id] = notes
        self._logger.info(f"笔记初始化: {thread_id}（激活事实 {len(facts)} 条）")
        return notes

    def update_notes(
        self, thread_id: str, delta_messages: list[Any]
    ) -> SessionNotes | None:
        """轮末 LLM 增量提炼笔记；无增量或提炼失败时保留旧笔记。

        Args:
            thread_id: 会话线程标识。
            delta_messages: 本轮新增消息（invoke 返回全量历史的尾部切片）。

        Returns:
            更新后的笔记；thread 无笔记或无增量消息时返回 None。
        """
        current = self.get_notes(thread_id)
        if current is None:
            return None
        transcript = _render_transcript(delta_messages)
        if not transcript:
            return current

        prompt = MarkdownPromptTemplate(
            "master_agent_notes_update.md",
            caller_file=__file__,
        ).format(notes_json=current.to_json(), transcript=transcript)
        try:
            response = self._llm.invoke(prompt)
            payload = _parse_notes_json(str(response.content))
            if payload is None:
                raise ValueError("笔记 JSON 解析失败")
            updated = _notes_from_dict(payload)
        except Exception as e:
            self._logger.warning(f"笔记提炼失败，保留旧笔记: {e}")
            with self._lock:
                self._notes[thread_id] = current
            return current

        with self._lock:
            self._notes[thread_id] = updated
        self._logger.info(f"笔记已更新: {thread_id}")
        return updated

    def render_notes(self, thread_id: str) -> str:
        """渲染 thread 笔记为注入块；无笔记返回空串。"""
        notes = self.get_notes(thread_id)
        return notes.render() if notes else ""

    def delete_notes(self, thread_id: str) -> None:
        """清理 thread 笔记（close_session 时调用）。"""
        with self._lock:
            self._notes.pop(thread_id, None)
        self._logger.info(f"笔记已清理: {thread_id}")


def _render_transcript(messages: list[Any]) -> str:
    """将本轮增量消息渲染为笔记提炼 transcript。

    Human/AI 取全文；ToolMessage 取摘要段（render 输出首段），
    过程细节不进入笔记输入。
    """
    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            lines.append(f"用户: {msg.content}")
        elif isinstance(msg, AIMessage):
            if msg.content:
                lines.append(f"助手: {msg.content}")
        elif isinstance(msg, ToolMessage):
            head = _tool_summary(str(msg.content))
            if head:
                lines.append(f"工具结果: {head}")
    return "\n".join(lines)


def _tool_summary(content: str) -> str:
    """提取工具输出首段摘要（ToolOutput.render 的 summary 部分）。"""
    body = content.split("<details>", 1)[0]
    lines = [line for line in body.splitlines() if line.strip()]
    return "\n".join(lines[:_TOOL_SUMMARY_LINES])


def _parse_notes_json(text: str) -> dict[str, Any] | None:
    """解析 LLM 笔记输出；剥离 ```json 围栏，失败返回 None。"""
    stripped = _JSON_FENCE_RE.sub("", text.strip()).strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _notes_from_dict(payload: dict[str, Any]) -> SessionNotes:
    """LLM JSON 构造笔记；字段类型不符时按空值兜底。"""

    def _str_list(key: str) -> list[str]:
        value = payload.get(key, [])
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str) and item.strip()]

    task = payload.get("task", "")
    return SessionNotes(
        task=task if isinstance(task, str) else "",
        facts=_str_list("facts"),
        symbols=_str_list("symbols"),
        constraints=_str_list("constraints"),
    )


def _thread_id_from_config(config: RunnableConfig | None) -> str:
    """从 LangGraph 运行时配置提取 thread_id（缺省 default）。"""
    if not config:
        return "default"
    configurable = config.get("configurable") or {}
    return str(configurable.get("thread_id", "default"))


def _notes_block(system_prompt: str, notes: str) -> str:
    """合并系统提示与笔记块。"""
    if not notes:
        return system_prompt
    if not system_prompt:
        return notes
    return f"{system_prompt}\n\n{notes}"


class SessionNotesMiddleware(AgentMiddleware):  # type: ignore[type-arg]
    """笔记注入中间件（ADR-024 §B）：每轮将 thread 笔记并入 system message。

    注入经 ``request.override`` 临时生效，不写入 checkpointer 历史；
    thread_id 经 ``get_config()`` 从 LangGraph 运行时上下文提取。
    """

    def __init__(self, engine: ContextEngine) -> None:
        self._engine = engine

    def wrap_model_call(self, request, handler):  # type: ignore[no-untyped-def]
        notes = self._engine.render_notes(_thread_id_from_config(get_config()))
        if not notes:
            return handler(request)
        base = (
            ""
            if request.system_message is None
            else str(request.system_message.content)
        )
        merged = SystemMessage(content=_notes_block(base, notes))
        return handler(request.override(system_message=merged))
