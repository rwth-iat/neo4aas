"""LangChain model factories pointed at the KIConnect (OpenAI-compatible) endpoint."""

from functools import lru_cache

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from config import (
    KICONNECT_API_KEY, KICONNECT_BASE_URL, MODEL_AGENT, MODEL_UTIL, MODEL_EMBED,
)


@lru_cache(maxsize=None)
def chat_model(model: str = MODEL_AGENT, temperature: float = 0.0) -> ChatOpenAI:
    """A ChatOpenAI bound to KIConnect. temperature=0 for deterministic tool decisions."""
    return ChatOpenAI(
        model=model,
        base_url=KICONNECT_BASE_URL,
        api_key=KICONNECT_API_KEY,
        temperature=temperature,
        timeout=60.0,
        max_retries=3,
    )


def util_model() -> ChatOpenAI:
    return chat_model(MODEL_UTIL)


@lru_cache(maxsize=None)
def embeddings() -> OpenAIEmbeddings:
    """KIConnect embeddings (qwen3-embedding-8b, dim 4096) for the RAG field index."""
    return OpenAIEmbeddings(
        model=MODEL_EMBED,
        base_url=KICONNECT_BASE_URL,
        api_key=KICONNECT_API_KEY,
        check_embedding_ctx_length=False,  # non-OpenAI model: skip tiktoken-based chunking
    )
