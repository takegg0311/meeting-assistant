from app.llm.base import AnswerSuggestionContext, ConversationTurn

SYSTEM_PROMPT = """あなたは会議中のユーザーを支援するアシスタントです。
会議の文字起こしが渡されます。末尾付近にユーザーへ向けられた問いがあるので、
それに対してユーザーがそのまま話せる回答案を作成してください。

- 日本語の話し言葉で、200文字程度を目安に簡潔にまとめる
- 前置きや相槌(「ご質問ありがとうございます」等)は入れず、回答の中身から始める
- 文字起こしは音声認識の出力なので、誤変換や言い直しが含まれている前提で解釈する
- どこが問いなのか判別できない場合は、直近の話題について確認すべき点を挙げる
- 文字起こしから読み取れない事実は補わず、その旨を短く添える
- 回答案の本文のみを出力し、見出しや説明を付けない"""


def _format_turns(turns: list[ConversationTurn]) -> str:
    lines = []
    for turn in turns:
        speaker = turn.speaker_id or "話者不明"
        lines.append(f"[{speaker}] {turn.text}")
    return "\n".join(lines)


def build_user_prompt(context: AnswerSuggestionContext) -> str:
    """回答提案用のユーザープロンプトを組み立てる。

    質問候補と文脈をセクションで分けて示し、「末尾の問いに答える」という
    指示だけを与える。境界の曖昧さはLLM側の解釈に委ねる方針(ARCHITECTURE 4.4)。
    """
    sections = []
    if context.context_turns:
        sections.append(
            "# これまでの会話(文脈)\n" + _format_turns(context.context_turns)
        )
    sections.append("# 直近の発言\n" + _format_turns(context.question_turns))
    sections.append("この直近の発言に含まれる、あなたに向けられた問いへの回答案を作成してください。")
    return "\n\n".join(sections)
