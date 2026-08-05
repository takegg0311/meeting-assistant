import asyncio
import base64
import contextlib
import json
import logging
import time
import uuid
from array import array
from typing import AsyncIterator

import websockets

from app.config import settings
from app.schemas import TranscriptEvent

logger = logging.getLogger(__name__)

# OpenAI Realtime APIは入力PCMのサンプルレートに24000Hz以上を要求する。
# システム全体は16000Hzで統一しているため、この境界でのみリサンプリングする。
_OPENAI_INPUT_SAMPLE_RATE = 24000


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
        self._last_sample: int = 0

    async def stream_transcribe(
        self, audio_chunks: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscriptEvent]:
        # transcriptionセッションでは ?model= を付けない(会話セッション用モデル選択と誤認され invalid_model になる)。
        # モデルは session.update の audio.input.transcription.model 側で指定する。
        url = f"{settings.openai_realtime_url}?intent=transcription"
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
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
                        "format": {"type": "audio/pcm", "rate": _OPENAI_INPUT_SAMPLE_RATE},
                        "transcription": {"model": settings.openai_realtime_model},
                        "turn_detection": {"type": "server_vad"},
                    }
                },
            },
        }

    async def _send_audio(self, ws, audio_chunks: AsyncIterator[bytes]) -> None:
        try:
            async for chunk in audio_chunks:
                resampled = self._resample_to_openai_rate(chunk)
                payload = {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(resampled).decode("ascii"),
                }
                await ws.send(json.dumps(payload))
        except websockets.exceptions.ConnectionClosed:
            logger.info("OpenAI Realtime connection closed while sending audio")

    def _resample_to_openai_rate(self, chunk: bytes) -> bytes:
        """16kHz PCM16LEチャンクを24kHzに線形補間でアップサンプルする。
        チャンク境界の不連続を避けるため、前チャンク末尾サンプルを補間の起点に使う。"""
        usable_len = len(chunk) - (len(chunk) % 2)
        samples = array("h")
        samples.frombytes(chunk[:usable_len])
        if len(samples) == 0:
            return b""

        ratio = settings.audio_sample_rate / _OPENAI_INPUT_SAMPLE_RATE  # 16000/24000
        extended = array("h", [self._last_sample]) + samples
        out_len = round((len(extended) - 1) / ratio)

        out = array("h", [0]) * out_len
        for i in range(out_len):
            src_pos = i * ratio
            idx = int(src_pos)
            frac = src_pos - idx
            if idx + 1 < len(extended):
                out[i] = int(extended[idx] * (1 - frac) + extended[idx + 1] * frac)
            else:
                out[i] = extended[idx]

        self._last_sample = samples[-1]
        return out.tobytes()

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
