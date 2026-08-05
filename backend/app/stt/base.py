from typing import AsyncIterator, Protocol

from app.schemas import TranscriptEvent


class SttProvider(Protocol):
    async def stream_transcribe(
        self, audio_chunks: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscriptEvent]: ...
