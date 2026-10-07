import asyncio
import base64
import contextlib
import json
import logging
import math
import time
import uuid
from array import array
from typing import AsyncIterator

import websockets

from app.config import settings
from app.schemas import TranscriptEvent
from app.stt.vad import SilenceDetector

logger = logging.getLogger(__name__)

# OpenAI Realtime APIは入力PCMのサンプルレートに24000Hz以上を要求する。
# システム全体は16000Hzで統一しているため、この境界でのみリサンプリングする。
_OPENAI_INPUT_SAMPLE_RATE = 24000


class OpenAiRealtimeConfigError(RuntimeError):
    pass


class OpenAiRealtimeSttProvider:
    """OpenAI Realtime API(WebSocket)にPCM16音声を転送し、
    共通の音量VADで音声を確定し、transcriptイベントを中継する。"""

    def __init__(self, vad_threshold_dbfs: float = -45.0) -> None:
        if not settings.openai_api_key:
            raise OpenAiRealtimeConfigError(
                "OPENAI_API_KEY が未設定です。OpenAI Realtime STTを使うには環境変数を設定してください。"
            )
        self._last_sample: int = 0
        self.vad_threshold_dbfs = vad_threshold_dbfs
        self._pending_commits = 0
        self._all_completed = asyncio.Event()
        self._all_completed.set()

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
                await sender_task
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
                        # 共通の音量VADでinput_audio_buffer.commitを送る。
                        "turn_detection": None,
                    }
                },
            },
        }

    async def _send_audio(self, ws, audio_chunks: AsyncIterator[bytes]) -> None:
        detector = SilenceDetector(settings.audio_sample_rate, self.vad_threshold_dbfs)
        sent_seconds = 0.0

        async def append(chunk: bytes):
            await ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(self._resample_to_openai_rate(chunk)).decode("ascii"),
            }))

        async def commit():
            # OpenAIは100ms未満のバッファを確定できないため、最後の短い発話を無音で補う。
            if sent_seconds < 0.1:
                await append(bytes(2 * math.ceil((0.1 - sent_seconds) * settings.audio_sample_rate)))
            self._pending_commits += 1
            self._all_completed.clear()
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))

        try:
            async for chunk in audio_chunks:
                boundary = detector.push(chunk)
                if not detector.has_speech:
                    detector.reset()
                    continue
                await append(chunk)
                sent_seconds += len(chunk) // 2 / settings.audio_sample_rate
                if boundary or sent_seconds >= 30.0:
                    await commit()
                    detector.reset()
                    sent_seconds = 0.0
            if sent_seconds:
                await commit()
            # 終了時も最後の確定結果を受信してからWebSocketを閉じる。
            await asyncio.wait_for(self._all_completed.wait(), timeout=10.0)
        finally:
            await ws.close()

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
        # 複数の確定音声は非同期に完了するためitem_idごとに文字起こしを保持する。
        items: dict[str, tuple[str, float, str]] = {}
        async for raw_message in ws:
            message = json.loads(raw_message)
            msg_type = message.get("type")
            if msg_type in (
                "conversation.item.input_audio_transcription.delta",
                "conversation.item.input_audio_transcription.completed",
            ):
                item_id = message["item_id"]
                segment_id, start_ts, text = items.get(item_id, (str(uuid.uuid4()), time.monotonic(), ""))
                final = msg_type.endswith(".completed")
                text = message.get("transcript", "") if final else text + message.get("delta", "")
                if final:
                    items.pop(item_id, None)
                    self._pending_commits = max(0, self._pending_commits - 1)
                    if not self._pending_commits:
                        self._all_completed.set()
                else:
                    items[item_id] = (segment_id, start_ts, text)
                if text:
                    yield TranscriptEvent(
                        segment_id=segment_id, audio_source="microphone", text=text,
                        is_final=final, start_ts=start_ts, end_ts=time.monotonic(),
                    )
            elif msg_type == "error" or msg_type == "conversation.item.input_audio_transcription.failed":
                raise RuntimeError(f"OpenAI Realtime transcription failed: {message.get('error')}")
