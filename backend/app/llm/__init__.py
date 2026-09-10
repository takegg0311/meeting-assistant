from app.llm.base import AnswerSuggestionContext, ConversationTurn, LlmProvider
from app.llm.mock import MockLlmProvider


def get_llm_provider(name: str) -> LlmProvider:
    if name == "mock":
        return MockLlmProvider()
    if name == "cloud_anthropic":
        from app.llm.anthropic_claude import AnthropicLlmProvider

        return AnthropicLlmProvider()
    raise ValueError(f"Unknown LLM provider: {name}")


__all__ = [
    "AnswerSuggestionContext",
    "ConversationTurn",
    "LlmProvider",
    "MockLlmProvider",
    "get_llm_provider",
]
