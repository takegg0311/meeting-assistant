import asyncio

from app.llm.base import AnswerSuggestionContext

_SIMULATED_LATENCY_SEC = 1.0


class MockLlmProvider:
    """開発・動作確認用のダミーLLM実装。

    APIキーなしで回答提案の配線(grace period・生成中カード・結果表示)を
    通しで確認できるようにする。実際の生成遅延に近い待機を入れる。
    """

    async def generate_answer_suggestion(self, context: AnswerSuggestionContext) -> str:
        await asyncio.sleep(_SIMULATED_LATENCY_SEC)

        question = " / ".join(turn.text for turn in context.question_turns)
        return (
            "【ダミー回答】直近の発言「"
            f"{question}"
            "」に対する回答案です。"
            f"文脈として{len(context.context_turns)}件の発言を参照しました。"
        )
