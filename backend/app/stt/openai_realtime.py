import asyncio
import base64
import contextlib
import json
import logging
import time
import uuid
from typing import AsyncIterator

import websockets

from app.config import settings
from app.schemas import TranscriptEvent

logger = logging.getLogger(__name__)


class OpenAiRealtimeConfigError(RuntimeError):
    pass


class OpenAiRealtimeSttProvider:
    """OpenAI Realtime API(WebSocket)にPCM16音声を転送し、
    サーバー側VAD(turn_detection)による確定/未確定transcriptイベントを中継する。"""

    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise OpenAiRealtimeConfigError(
                "OPENAI_API_KEY が未設定です。OpenAI Realtime STTを使うには環境変数を設定してください。"
            )

    async def stream_transcribe(
        self, audio_chunks: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscriptEvent]:
        url = f"{settings.openai_realtime_url}?model={settings.openai_realtime_model}"
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "OpenAI-Beta": "realtime=v1",
        }

        async with websockets.connect(url, additional_headers=headers, max_size=None) as ws:
            await ws.send(json.dumps(self._build_session_update()))

            sender_task = asyncio.create_task(self._send_audio(ws, audio_chunks))
            try:
                async for event in self._receive_events(ws):
                    yield event
            finally:
                sender_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sender_task

    def _build_session_update(self) -> dict:
        return {
            "type": "session.update",
            "session": {
                "type": "transcription",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": settings.audio_sample_rate},
                        "transcription": {"model": settings.openai_realtime_model},
                        "turn_detection": {"type": "server_vad"},
                    }
                },
            },
        }

    async def _send_audio(self, ws, audio_chunks: AsyncIterator[bytes]) -> None:
        try:
            async for chunk in audio_chunks:
                payload = {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(chunk).decode("ascii"),
                }
                await ws.send(json.dumps(payload))
        except websockets.exceptions.ConnectionClosed:
            logger.info("OpenAI Realtime connection closed while sending audio")

    async def _receive_events(self, ws) -> AsyncIterator[TranscriptEvent]:
        segment_start_ts = time.monotonic()
        current_segment_id = str(uuid.uuid4())
        accumulated_text = ""

        async for raw_message in ws:
            message = json.loads(raw_message)
            msg_type = message.get("type")

            if msg_type == "conversation.item.input_audio_transcription.delta":
                delta = message.get("delta", "")
                if delta:
                    accumulated_text += delta
                    yield TranscriptEvent(
                        segment_id=current_segment_id,
                        audio_source="microphone",
                        text=accumulated_text,
                        is_final=False,
                        start_ts=segment_start_ts,
                        end_ts=time.monotonic(),
                    )
            elif msg_type == "conversation.item.input_audio_transcription.completed":
                transcript = message.get("transcript", "")
                if transcript:
                    yield TranscriptEvent(
                        segment_id=current_segment_id,
                        audio_source="microphone",
                        text=transcript,
                        is_final=True,
                        start_ts=segment_start_ts,
                        end_ts=time.monotonic(),
                    )
                current_segment_id = str(uuid.uuid4())
                segment_start_ts = time.monotonic()
                accumulated_text = ""
            elif msg_type == "error":
                logger.error("OpenAI Realtime error: %s", message.get("error"))
