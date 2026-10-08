import logging

from anthropic import AsyncAnthropic

from app.config import settings
from app.llm.base import AnswerSuggestionContext
from app.llm.prompt import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)


class AnthropicLlmProvider:
    """Claude APIによる回答提案生成。

    2〜3秒目標のレイテンシ要求に合わせ、既定では軽量モデル(Haiku)を使う
    (ARCHITECTURE 4.4)。モデルは ANTHROPIC_ANSWER_MODEL で差し替え可能。
    """

    def __init__(self) -> None:
        if not settings.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set (required for llm_provider=cloud_anthropic)"
            )
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_answer_model
        self._max_tokens = settings.anthropic_answer_max_tokens

    async def generate_answer_suggestion(self, context: AnswerSuggestionContext) -> str:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_user_prompt(context)}],
        )

        texts = [block.text for block in response.content if block.type == "text"]
        answer = "\n".join(texts).strip()
        if not answer:
            raise ValueError("Claude API returned no text content")
        return answer
