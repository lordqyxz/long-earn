"""LLM 服务实现

封装 LLM 的创建和调用，提供统一的接口。
"""

from typing import TYPE_CHECKING, Any

from langchain_core.language_models import BaseLanguageModel

from long_earn.services import LLMService, LoggerService
from long_earn.utils.llm_factory import create_llm

if TYPE_CHECKING:
    from long_earn.config import AppConfig


class LLMServiceImpl(LLMService):
    """LLM 服务实现

    参考 LangGraph Runtime 实践：
    1. 依赖通过 context 传递，而非硬编码
    2. 支持懒加载，提高启动性能
    3. 生命周期由 context 管理

    可靠性：每次 invoke 不复用长连接 LLM 实例，避免 langchain_ollama / ollama-python /
    httpx stream context manager 在多次调用后内部状态损坏触发 Fatal Python error
    (PyEval_SaveThread GIL is released)。该错误在 e2e 多轮演进时实测会让进程崩溃。
    重建实例代价小（~50ms），换来跨轮 LLM 调用的可靠性。

    用法:
        # 在节点函数中
        def my_node(state: State, context: RuntimeContext):
            llm_service = context.get("llm_service")
            response = llm_service.invoke(prompt)
    """

    # 普通 Exception（连接超时、HTTP 5xx 等）触发 1 次重试；
    # Fatal Python error 进程级信号 Python 层无法捕获救援。
    _MAX_RETRIES = 1

    def __init__(self, config: "AppConfig", logger: LoggerService):
        """初始化 LLM 服务

        Args:
            config: 应用配置（含 llm_type / llm_model / llm_base_url）
            logger: 日志服务
        """
        self.config = config
        self.logger = logger
        self._llm: BaseLanguageModel | None = None
        self._invoke_count: int = 0

    def _build_llm(self) -> BaseLanguageModel:
        """构造一个新的 LLM 实例（不复用）"""
        return create_llm(
            llm_type=self.config.llm_type,
            model_name=self.config.llm_model,
            base_url=self.config.llm_base_url,
        )

    @property
    def llm(self) -> BaseLanguageModel:
        """获取 LLM 实例（首次构造后整个 session 复用）

        注意：本属性主要给 get_llm() 暴露给少数需要直接拿底层 LLM 的旧调用者，
        invoke() 不再走这条路径——它每次自己 _build_llm 避免连接累积。
        """
        if self._llm is None:
            self._llm = self._build_llm()
        return self._llm

    def _bind_format(self, llm: BaseLanguageModel, format: str) -> BaseLanguageModel:
        """根据 format 参数绑定模型配置"""
        if format != "json":
            return llm
        if self.config.llm_type == "ollama":
            return llm.bind(format="json")
        if self.config.llm_type in ("dashscope", "openai"):
            return llm.bind(response_format={"type": "json_object"})
        return llm

    def invoke(self, prompt: str, format: str = "") -> Any:
        """调用 LLM

        每次都构造新 LLM 实例（避免长连接累积错误）+ 普通异常重试 1 次。

        Args:
            prompt: 提示词
            format: 输出格式，可选 "json" 强制 JSON 输出

        Returns:
            LLM 响应
        """
        self._invoke_count += 1
        call_id = self._invoke_count

        self.logger.debug(f"LLM 调用 #{call_id} 开始...")

        last_exc: Exception | None = None
        for attempt in range(1 + self._MAX_RETRIES):
            try:
                # 关键：每次重建 LLM 实例，避免 ChatOllama 内部 httpx client
                # 长连接状态损坏触发 Fatal GIL 错误（实测见 memory 索引 dsl-shift / xtquant）
                llm = self._build_llm()
                llm = self._bind_format(llm, format)
                response = llm.invoke(prompt)
                content_preview = (
                    response.content[:100]
                    if hasattr(response, "content")
                    else str(response)[:100]
                )
                self.logger.debug(
                    f"LLM 调用 #{call_id} 完成（第{attempt + 1}次尝试），"
                    f"响应预览: {content_preview!r}"
                )
                return response
            except Exception as e:
                last_exc = e
                if attempt < self._MAX_RETRIES:
                    self.logger.warning(
                        f"LLM 调用 #{call_id} 第{attempt + 1}次失败: {e}，重试中..."
                    )
                    continue
                self.logger.error(
                    f"LLM 调用 #{call_id} 共{attempt + 1}次失败，最后异常: {e}"
                )
                raise
        # for...else 等价：循环跑完没 return 表示全部失败上抛
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("LLM invoke 未执行任何尝试")  # pragma: no cover

    def get_llm(self) -> BaseLanguageModel:
        """获取底层 LLM 实例（兼容旧调用者）

        注意：调用此方法返回的实例可能已被多次复用——只供需要拿底层句柄的极少
        数场景。生产中请直接用 invoke() 走重建+重试路径。
        """
        return self.llm
