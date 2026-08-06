"""LLM 工厂：经 OpenAI 兼容接口接入（默认 DeepSeek）。"""

from langchain_openai import ChatOpenAI

from app.core.config import settings


def get_llm(temperature: float = 0.3) -> ChatOpenAI:
    """返回 ChatOpenAI（OpenAI 兼容；默认指向 DeepSeek）。"""
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        temperature=temperature,
    )
