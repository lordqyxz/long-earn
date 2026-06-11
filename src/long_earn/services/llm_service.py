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

    用法:
        # 在节点函数中
        def my_node(state: State, context: RuntimeContext):
            llm_service = context.get("llm_service")
            response = llm_service.invoke(prompt)
    """

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

    @property
    def llm(self) -> BaseLanguageModel:
        """获取 LLM 实例（懒加载）

        Returns:
            LLM 实例
        """
        if self._llm is None:
            self._llm = create_llm(
                llm_type=self.config.llm_type,
                model_name=self.config.llm_model,
                base_url=self.config.llm_base_url,
            )
        return self._llm

    def invoke(self, prompt: str, format: str = "") -> Any:
        """调用 LLM

        Args:
            prompt: 提示词
            format: 输出格式，可选 "json" 强制 JSON 输出

        Returns:
            LLM 响应
        """
        self._invoke_count += 1
        call_id = self._invoke_count

        self.logger.debug(f"LLM 调用 #{call_id} 开始...")

        try:
            # 根据 format 参数绑定模型配置
            llm = self.llm
            if format == "json":
                if self.config.llm_type == "ollama":
                    # Ollama 原生支持 format="json"
                    llm = self.llm.bind(format="json")
                elif self.config.llm_type in ("dashscope", "openai"):
                    # OpenAI 兼容 API 使用 response_format
                    llm = self.llm.bind(response_format={"type": "json_object"})

            response = llm.invoke(prompt)
            content_preview = (
                response.content[:100]
                if hasattr(response, "content")
                else str(response)[:100]
            )
            self.logger.debug(
                f"LLM 调用 #{call_id} 完成，响应预览: {content_preview!r}"
            )
            return response
        except Exception as e:
            self.logger.error(f"LLM 调用 #{call_id} 异常: {e}")
            raise

    def get_llm(self) -> BaseLanguageModel:
        """获取底层 LLM 实例

        Returns:
            LLM 实例
        """
        return self.llm
