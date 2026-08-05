import time
import uuid
from typing import AsyncIterator

from app.schemas import TranscriptEvent

_DUMMY_PHRASES = [
    "こんにちは、本日はお集まりいただきありがとうございます。",
    "それでは最初の議題について説明します。",
    "こちらのグラフをご覧ください。",
    "ご質問があればお願いします。",
    "次回のミーティングは来週を予定しています。",
]

_CHUNKS_PER_SEGMENT = 20


class MockSttProvider:
    """開発・動作確認用のダミーSTT実装。受信した音声チャンク数に応じてダミーのtranscriptを返す。"""

    async def stream_transcribe(
        self, audio_chunks: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscriptEvent]:
        chunk_count = 0
        segment_index = 0
        start_ts = time.monotonic()

        async for _chunk in audio_chunks:
            chunk_count += 1
            if chunk_count % _CHUNKS_PER_SEGMENT != 0:
                continue

            phrase = _DUMMY_PHRASES[segment_index % len(_DUMMY_PHRASES)]
            segment_id = str(uuid.uuid4())
            now = time.monotonic()

            yield TranscriptEvent(
                segment_id=segment_id,
                audio_source="microphone",
                text=phrase,
                is_final=True,
                start_ts=start_ts,
                end_ts=now,
            )
            segment_index += 1
            start_ts = now
