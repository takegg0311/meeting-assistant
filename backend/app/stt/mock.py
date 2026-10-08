import time
import uuid
from typing import AsyncIterator

from app.config import settings
from app.schemas import TranscriptEvent
from app.stt.vad import SilenceSegmenter

_DUMMY_PHRASES = [
    "こんにちは、本日はお集まりいただきありがとうございます。",
    "それでは最初の議題について説明します。",
    "こちらのグラフをご覧ください。",
    "ご質問があればお願いします。",
    "次回のミーティングは来週を予定しています。",
]


class MockSttProvider:
    """開発・動作確認用のダミーSTT実装。共通VADの発話区切りごとにダミーのtranscriptを返す。"""

    def __init__(self, vad_threshold_dbfs: float = -45.0) -> None:
        self._segmenter = SilenceSegmenter(settings.audio_sample_rate, vad_threshold_dbfs)

    async def stream_transcribe(
        self, audio_chunks: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscriptEvent]:
        segment_index = 0
        start_ts = time.monotonic()
        async for chunk in audio_chunks:
            if self._segmenter.push(chunk) is not None:
                yield self._event(segment_index, start_ts)
                segment_index += 1
                start_ts = time.monotonic()
        if self._segmenter.flush_remaining() is not None:
            yield self._event(segment_index, start_ts)

    def _event(self, index: int, start_ts: float) -> TranscriptEvent:
        return TranscriptEvent(
            segment_id=str(uuid.uuid4()), audio_source="microphone",
            text=_DUMMY_PHRASES[index % len(_DUMMY_PHRASES)], is_final=True,
            start_ts=start_ts, end_ts=time.monotonic(),
        )
