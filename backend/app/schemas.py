from typing import Literal

from pydantic import BaseModel

AudioSource = Literal["microphone", "tab_audio", "system_audio"]
SttProviderName = Literal["cloud", "local", "mock"]


class StartSessionMessage(BaseModel):
    type: Literal["start_session"] = "start_session"
    stt_provider: SttProviderName = "mock"
    audio_source: AudioSource = "microphone"
    features: list[str] = []


class StopSessionMessage(BaseModel):
    type: Literal["stop_session"] = "stop_session"


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
