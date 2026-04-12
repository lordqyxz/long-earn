"""LLM 服务实现

封装 LLM 的创建和调用，提供统一的接口。
"""

from typing import TYPE_CHECKING, Any

from langchain_core.language_models import BaseLanguageModel

from long_earn.services import LLMService
from long_earn.utils.llm_factory import create_llm

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext


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

    def __init__(self, context: "RuntimeContext"):
        """初始化 LLM 服务

        Args:
            context: 运行时上下文，从中获取配置
        """
        self.context = context
        self._llm: BaseLanguageModel | None = None

    @property
    def llm(self) -> BaseLanguageModel:
        """获取 LLM 实例（懒加载）

        Returns:
            LLM 实例
        """
        if self._llm is None:
            config = self.context.config
            self._llm = create_llm(
                llm_type=config.llm_type,
                model_name=config.llm_model,
                base_url=config.llm_base_url,
            )
        return self._llm

    def invoke(self, prompt: str) -> Any:
        """调用 LLM

        Args:
            prompt: 提示词

        Returns:
            LLM 响应
        """
        return self.llm.invoke(prompt)

    def get_llm(self) -> BaseLanguageModel:
        """获取底层 LLM 实例

        Returns:
            LLM 实例
        """
        return self.llm
