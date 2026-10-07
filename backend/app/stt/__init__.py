from app.stt.base import SttProvider
from app.stt.mock import MockSttProvider


def get_stt_provider(name: str, *, vad_threshold_dbfs: float = -45.0) -> SttProvider:
    if name == "mock":
        return MockSttProvider(vad_threshold_dbfs=vad_threshold_dbfs)
    if name == "cloud_openai":
        from app.stt.openai_realtime import OpenAiRealtimeSttProvider

        return OpenAiRealtimeSttProvider(vad_threshold_dbfs=vad_threshold_dbfs)
    if name == "cloud_google":
        from app.stt.google_cloud import GoogleCloudSttProvider

        return GoogleCloudSttProvider(vad_threshold_dbfs=vad_threshold_dbfs)
    if name == "local_whispercpp":
        from app.stt.whisper_cpp import WhisperCppSttProvider

        return WhisperCppSttProvider(vad_threshold_dbfs=vad_threshold_dbfs)
    raise ValueError(f"Unknown STT provider: {name}")


__all__ = ["SttProvider", "MockSttProvider", "get_stt_provider"]
