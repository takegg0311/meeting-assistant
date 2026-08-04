from app.stt.base import SttProvider
from app.stt.mock import MockSttProvider


def get_stt_provider(name: str) -> SttProvider:
    if name in ("mock", "cloud", "local"):
        # Cloud/LocalのSTT実装は別Issueで追加予定。現時点ではMockにフォールバックする。
        return MockSttProvider()
    raise ValueError(f"Unknown STT provider: {name}")


__all__ = ["SttProvider", "MockSttProvider", "get_stt_provider"]
