from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ConversationTurn:
    """LLMに渡す会話の1ターン。transcriptセグメントから組み立てる。"""

    segment_id: str
    speaker_id: str | None
    text: str


@dataclass
class AnswerSuggestionContext:
    """回答提案の生成コンテキスト。

    「どこからどこまでが質問か」の境界は厳密に決めず、直近ターンを質問候補
    (`question_turns`)、それ以前を文脈(`context_turns`)として分けて渡し、
    最終的な解釈はプロンプト側に委ねる(ARCHITECTURE 4.4)。
    """

    question_turns: list[ConversationTurn]
    context_turns: list[ConversationTurn] = field(default_factory=list)

    @property
    def source_segment_ids(self) -> list[str]:
        return [turn.segment_id for turn in self.context_turns + self.question_turns]


class LlmProvider(Protocol):
    async def generate_answer_suggestion(self, context: AnswerSuggestionContext) -> str: ...
