"""BasePersona 基类 — ADR-012 Phase 1

提供大师 Persona 的公用能力：
- ``__init__(llm)``：接收底层 ChatModel（与 RuntimeContext.require_llm().get_llm() 一致），
  降低对 RuntimeContext 的耦合
- ``_load_prompt(mode)``：从 prompts/<name>/<mode>.md 加载 MarkdownChatPromptTemplate
- ``_parse_result(response, mode)``：解析 LLM 响应为 PersonaResult
- ``analyze(context)``：模板方法，校验 mode 支持后派发到子类 _do_analyze

子类需实现：
- 类属性 name / display_name / perspective / supported_modes
- ``_do_analyze(context) -> PersonaResult``
"""

from __future__ import annotations

from typing import Any

from long_earn.core.chat_prompt_loader import MarkdownChatPromptTemplate
from long_earn.core.llm_utils import parse_llm_json
from long_earn.skills.personas.protocol import PersonaContext, PersonaResult

# stock_analysis 模式下从 LLM 文本中提取结论的关键词（按优先级）
_STOCK_VERDICT_KEYWORDS: tuple[str, ...] = ("买入", "持有", "卖出")

# strategy_review 模式下 verdict 合法取值
_STRATEGY_VERDICT_KEYWORDS: tuple[str, ...] = ("接受", "改进", "拒绝")

# strategy_generate 模式下 verdict 合法取值
_STRATEGY_GENERATE_VERDICT_KEYWORDS: tuple[str, ...] = ("推荐", "谨慎", "不推荐")


class BasePersona:
    """大师 Persona 基类。

    子类通过类属性声明身份与支持的模式，``analyze`` 作为模板方法统一校验
    mode 是否在 ``supported_modes`` 内，未支持的模式抛 NotImplementedError。
    """

    # 子类必填类属性
    name: str = ""
    display_name: str = ""
    perspective: str = ""

    # 默认仅支持 stock_analysis；strategy_* 模式由阶段 2/3 接入
    supported_modes: tuple[str, ...] = ("stock_analysis",)

    def __init__(self, llm: Any) -> None:
        """初始化大师 Persona。

        Args:
            llm: 底层 ChatModel 实例（langchain BaseChatModel），
                 与现有分析师 ``context.require_llm().get_llm()`` 返回值一致
        """
        self.llm = llm
        # mode -> MarkdownChatPromptTemplate 缓存（懒加载）
        self._prompts: dict[str, MarkdownChatPromptTemplate] = {}

    def _load_prompt(self, mode: str) -> MarkdownChatPromptTemplate:
        """加载指定模式的 prompt 模板。

        prompt 文件位于与本模块同目录的 ``prompts/<name>/<mode>.md``。
        使用 ``__file__``（base.py 位于 personas/ 目录）解析相对路径。

        Args:
            mode: PersonaMode 之一

        Returns:
            MarkdownChatPromptTemplate 实例
        """
        if mode not in self._prompts:
            self._prompts[mode] = MarkdownChatPromptTemplate(
                template_file=f"prompts/{self.name}/{mode}.md",
                caller_file=__file__,
            )
        return self._prompts[mode]

    def _parse_result(self, response: Any, mode: str) -> PersonaResult:
        """解析 LLM 响应为 PersonaResult。

        stock_analysis 模式下 LLM 返回自由文本，从中尝试提取
        买入/持有/卖出 结论关键词；无法识别时 verdict 为 "未知"，
        完整文本保留在 raw_analysis / rationale。

        strategy_review 模式下 LLM 返回 JSON，含 verdict / rationale /
        weaknesses / suggestions / confidence 字段；解析失败时退化为
        保留原文的 "未知" 结果。

        strategy_generate 模式下 LLM 返回 JSON，含 verdict / rationale /
        suggestions / confidence 字段（无 weaknesses）；解析失败时同样
        退化为保留原文的 "未知" 结果。

        Args:
            response: LLM invoke 返回值（具有 .content 属性）
            mode: 调用模式

        Returns:
            PersonaResult
        """
        content = response.content if hasattr(response, "content") else str(response)

        if mode == "strategy_review":
            return self._parse_strategy_review(content)

        if mode == "strategy_generate":
            return self._parse_strategy_generate(content)

        verdict = "未知"
        if mode == "stock_analysis":
            for keyword in _STOCK_VERDICT_KEYWORDS:
                if keyword in content:
                    verdict = keyword
                    break

        return PersonaResult(
            verdict=verdict,
            rationale=content,
            raw_analysis=content,
        )

    @staticmethod
    def _parse_strategy_review(content: str) -> PersonaResult:
        """解析 strategy_review 模式的 LLM JSON 输出。

        JSON schema:
        {"verdict": "接受/改进/拒绝", "rationale": "...",
         "weaknesses": [...], "suggestions": [...], "confidence": 0.0-1.0}

        解析失败（LLM 未返回有效 JSON）时，退化为 verdict="未知"，
        rationale / raw_analysis 保留原始文本，避免阻塞反思流程。
        """
        try:
            data = parse_llm_json(content)
        except Exception:
            return PersonaResult(
                verdict="未知",
                rationale=content,
                raw_analysis=content,
            )

        if not isinstance(data, dict):
            return PersonaResult(
                verdict="未知",
                rationale=content,
                raw_analysis=content,
            )

        verdict = str(data.get("verdict", "")).strip()
        if verdict not in _STRATEGY_VERDICT_KEYWORDS:
            verdict = "未知"

        weaknesses = data.get("weaknesses") or []
        if not isinstance(weaknesses, list):
            weaknesses = [str(weaknesses)]
        else:
            weaknesses = [str(w) for w in weaknesses]

        suggestions = data.get("suggestions") or []
        if not isinstance(suggestions, list):
            suggestions = [str(suggestions)]
        else:
            suggestions = [str(s) for s in suggestions]

        try:
            confidence = float(data.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        return PersonaResult(
            verdict=verdict,
            rationale=str(data.get("rationale", "")),
            weaknesses=weaknesses,
            suggestions=suggestions,
            confidence=confidence,
            raw_analysis=content,
        )

    @staticmethod
    def _parse_strategy_generate(content: str) -> PersonaResult:
        """解析 strategy_generate 模式的 LLM JSON 输出。

        JSON schema:
        {"verdict": "推荐/谨慎/不推荐", "rationale": "...",
         "suggestions": [...], "confidence": 0.0-1.0}

        解析失败（LLM 未返回有效 JSON）时，退化为 verdict="未知"，
        rationale / raw_analysis 保留原始文本，避免阻塞策略生成流程。
        """
        try:
            data = parse_llm_json(content)
        except Exception:
            return PersonaResult(
                verdict="未知",
                rationale=content,
                raw_analysis=content,
            )

        if not isinstance(data, dict):
            return PersonaResult(
                verdict="未知",
                rationale=content,
                raw_analysis=content,
            )

        verdict = str(data.get("verdict", "")).strip()
        if verdict not in _STRATEGY_GENERATE_VERDICT_KEYWORDS:
            verdict = "未知"

        suggestions = data.get("suggestions") or []
        if not isinstance(suggestions, list):
            suggestions = [str(suggestions)]
        else:
            suggestions = [str(s) for s in suggestions]

        try:
            confidence = float(data.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        return PersonaResult(
            verdict=verdict,
            rationale=str(data.get("rationale", "")),
            suggestions=suggestions,
            confidence=confidence,
            raw_analysis=content,
        )

    def analyze(self, context: PersonaContext) -> PersonaResult:
        """模板方法：校验 mode 后派发到子类 _do_analyze。

        Args:
            context: PersonaContext

        Raises:
            NotImplementedError: mode 不在 supported_modes 内
        """
        if context.mode not in self.supported_modes:
            raise NotImplementedError(
                f"{self.name} persona 不支持模式: {context.mode}"
                f"（支持: {list(self.supported_modes)}）"
            )
        return self._do_analyze(context)

    def _do_analyze(self, context: PersonaContext) -> PersonaResult:
        """子类实现：执行实际分析并返回 PersonaResult。"""
        raise NotImplementedError(
            f"{self.name} persona 未实现 _do_analyze"
        )
