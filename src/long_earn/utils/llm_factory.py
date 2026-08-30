import os

from langchain_core.language_models import BaseLanguageModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

# 默认超时（秒）
DEFAULT_TIMEOUT = 300


def _env_api_key(name: str) -> str:
    """读取必填 API Key 环境变量。"""
    value = os.getenv(name)
    if not value:
        raise ValueError(f"缺少环境变量 {name}")
    return value


def create_llm(
    llm_type: str = "deepseek",
    model_name: str | None = None,
    base_url: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    **kwargs,
) -> BaseLanguageModel:
    """根据类型创建LLM实例

    Args:
        llm_type: LLM类型，可选值: deepseek, ollama, dashscope, openai
        model_name: 模型名称，如果不提供则使用各类型默认值
        base_url: 自定义API基础URL（用于OpenAI兼容模型）
        timeout: 请求超时时间（秒），默认300秒
        **kwargs: 额外参数

    Returns:
        初始化好的LLM实例
    """
    if llm_type == "deepseek":
        return ChatOpenAI(
            model=model_name or "deepseek-v4-flash",
            api_key=SecretStr(_env_api_key("DEEPSEEK_API_KEY")),
            base_url=base_url or "https://api.deepseek.com/v1",
            timeout=timeout,
            **kwargs,
        )

    if llm_type == "ollama":
        return ChatOllama(
            model=model_name or "deepseek-v4-flash",
            client_kwargs={
                "timeout": timeout,
            },
            **kwargs,
        )

    if llm_type == "dashscope":
        # 阿里云DashScope模型
        # 注意：使用DashScope需要设置环境变量 DASHSCOPE_API_KEY
        return ChatOpenAI(
            model=model_name or "qwen-plus",
            api_key=SecretStr(_env_api_key("DASHSCOPE_API_KEY")),
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            timeout=timeout,
            **kwargs,
        )

    if llm_type == "openai":
        # OpenAI 兼容模型；未指定 base_url 时默认 LM Studio 本地地址
        return ChatOpenAI(
            model=model_name or "gpt-3.5-turbo",
            base_url=base_url or "http://localhost:11434/v1",
            timeout=timeout,
            **kwargs,
        )

    raise ValueError(f"不支持的LLM类型: {llm_type}")
