from typing import Literal

from pydantic import BaseModel

AudioSource = Literal["microphone", "tab_audio", "system_audio"]
SttProviderName = Literal["cloud_openai", "cloud_google", "local_whispercpp", "mock"]
LlmProviderName = Literal["cloud_anthropic", "mock"]


class StartSessionMessage(BaseModel):
    type: Literal["start_session"] = "start_session"
    stt_provider: SttProviderName = "mock"
    audio_source: AudioSource = "microphone"
    features: list[str] = []


class StopSessionMessage(BaseModel):
    type: Literal["stop_session"] = "stop_session"


class RequestAnswerSuggestionMessage(BaseModel):
    """「回答提案」ボタン押下。押下時点ではSTTが未確定な可能性が高いため、
    サーバー側で確定セグメントを短時間待ってから生成する(grace period)。"""

    type: Literal["request_answer_suggestion"] = "request_answer_suggestion"
    request_id: str


class TranscriptEvent(BaseModel):
    type: Literal["transcript"] = "transcript"
    segment_id: str
    speaker_id: str | None = None
    audio_source: AudioSource
    text: str
    is_final: bool
    start_ts: float
    end_ts: float


class StatusEvent(BaseModel):
    type: Literal["status"] = "status"
    stage: Literal["stt_connected", "processing", "error", "session_stopped"]
    message: str = ""


class AnswerSuggestionEvent(BaseModel):
    """回答提案の生成状況と結果。1リクエストにつき generating → done|error の順で配信する。"""

    type: Literal["answer_suggestion"] = "answer_suggestion"
    request_id: str
    status: Literal["generating", "done", "error"]
    answer: str = ""
    # 回答の根拠にしたtranscriptセグメント(質問候補 + 文脈)
    source_segment_ids: list[str] = []
    message: str = ""
