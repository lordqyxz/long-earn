from langchain_community.chat_models import ChatOllama
from langchain_core.language_models import BaseLanguageModel
from langchain_openai import ChatOpenAI


def create_llm(
    llm_type: str = "ollama",
    model_name: str = "qwen3.5:cloud",
    base_url: str = "http://localhost:11434",
    **kwargs,
) -> BaseLanguageModel:
    """
    根据类型创建LLM实例

    Args:
        llm_type: LLM类型，可选值: ollama, dashscope, openai
        model_name: 模型名称，如果不提供则使用默认值
        base_url: 自定义API基础URL（用于OpenAI兼容模型）
        **kwargs: 额外参数

    Returns:
        初始化好的LLM实例
    """
    if llm_type == "ollama":
        # 默认使用ollama的qwen3.5:cloud模型
        if model_name is None:
            model_name = "qwen3.5:cloud"
        return ChatOllama(model=model_name, **kwargs)

    elif llm_type == "dashscope":
        # 阿里云DashScope模型
        if model_name is None:
            model_name = "qwen-turbo"
        # 注意：使用DashScope需要设置环境变量
        # DASHSCOPE_API_KEY
        return ChatOpenAI(
            model=model_name,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            **kwargs,
        )

    elif llm_type == "openai":
        # OpenAI兼容模型
        if model_name is None:
            model_name = "gpt-3.5-turbo"
        # 支持自定义base_url，如果不提供则使用OpenAI官方地址
        if base_url is None:
            base_url = "https://api.openai.com/v1"
        # 注意：使用OpenAI需要设置环境变量
        # OPENAI_API_KEY
        return ChatOpenAI(model=model_name, base_url=base_url, **kwargs)

    else:
        raise ValueError(f"不支持的LLM类型: {llm_type}")
