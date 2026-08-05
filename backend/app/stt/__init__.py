from app.stt.base import SttProvider
from app.stt.mock import MockSttProvider


def get_stt_provider(name: str) -> SttProvider:
    if name == "mock":
        return MockSttProvider()
    if name == "cloud_openai":
        from app.stt.openai_realtime import OpenAiRealtimeSttProvider

        return OpenAiRealtimeSttProvider()
    if name == "cloud_google":
        from app.stt.google_cloud import GoogleCloudSttProvider

        return GoogleCloudSttProvider()
    if name == "local_whispercpp":
        from app.stt.whisper_cpp import WhisperCppSttProvider

        return WhisperCppSttProvider()
    raise ValueError(f"Unknown STT provider: {name}")


__all__ = ["SttProvider", "MockSttProvider", "get_stt_provider"]
