import asyncio
import logging
from typing import AsyncIterator

from fastapi import WebSocket

from app.schemas import AudioSource, StatusEvent, TranscriptEvent
from app.stt import get_stt_provider

logger = logging.getLogger(__name__)

_QUEUE_DONE = object()


class MeetingSession:
    """WebSocket接続ごとに1つ保持し、音声受信→STT→transcript配信を仲介する。"""

    def __init__(self, websocket: WebSocket, stt_provider_name: str, audio_source: AudioSource):
        self._websocket = websocket
        self._audio_source = audio_source
        self._stt_provider = get_stt_provider(stt_provider_name)
        self._audio_queue: asyncio.Queue[bytes | object] = asyncio.Queue()
        self._stt_task: asyncio.Task | None = None

    async def start(self) -> None:
        await self._send(StatusEvent(stage="stt_connected", message="STT provider ready"))
        self._stt_task = asyncio.create_task(self._run_stt_loop())

    async def push_audio_chunk(self, chunk: bytes) -> None:
        await self._audio_queue.put(chunk)

    async def stop(self) -> None:
        await self._audio_queue.put(_QUEUE_DONE)
        if self._stt_task is not None:
            await self._stt_task
        await self._send(StatusEvent(stage="session_stopped", message="Session stopped"))

    async def _audio_chunk_iterator(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._audio_queue.get()
            if item is _QUEUE_DONE:
                return
            yield item  # type: ignore[misc]

    async def _run_stt_loop(self) -> None:
        try:
            async for event in self._stt_provider.stream_transcribe(self._audio_chunk_iterator()):
                event.audio_source = self._audio_source
                await self.on_final_segment(event)
        except Exception:
            logger.exception("STT loop failed")
            await self._send(StatusEvent(stage="error", message="STT processing failed"))

    async def on_final_segment(self, segment: TranscriptEvent) -> None:
        await self._send(segment)  # 優先度1: 即座に配信
        # 優先度2〜5(ファクトチェック・回答提案・想定質問生成・話者判別)は後続Phaseで
        # asyncio.create_task(...) として非同期・fire-and-forgetで追加していく。

    async def _send(self, message) -> None:
        await self._websocket.send_json(message.model_dump())
