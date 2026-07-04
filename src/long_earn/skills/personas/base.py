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
from long_earn.skills.personas.protocol import PersonaContext, PersonaResult

# stock_analysis 模式下从 LLM 文本中提取结论的关键词（按优先级）
_STOCK_VERDICT_KEYWORDS: tuple[str, ...] = ("买入", "持有", "卖出")


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

        Args:
            response: LLM invoke 返回值（具有 .content 属性）
            mode: 调用模式

        Returns:
            PersonaResult
        """
        content = response.content if hasattr(response, "content") else str(response)

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
