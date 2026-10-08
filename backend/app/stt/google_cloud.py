import asyncio
import logging
import time
import uuid
from typing import AsyncIterator

from google.cloud import speech_v1

from app.config import settings
from app.schemas import TranscriptEvent
from app.stt.vad import SilenceDetector

logger = logging.getLogger(__name__)


class GoogleCloudSttProvider:
    """無音区間でStreamingRecognizeを更新し、各接続を4分以内に収める。"""

    STREAM_LIMIT_SECONDS = 240.0
    MIN_STREAM_SECONDS = 10.0
    SILENCE_SECONDS = 0.8

    def __init__(self, vad_threshold_dbfs: float = -45.0) -> None:
        self._client = speech_v1.SpeechAsyncClient()
        self.vad_threshold_dbfs = vad_threshold_dbfs

    async def stream_transcribe(
        self, audio_chunks: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscriptEvent]:
        streaming_config = speech_v1.StreamingRecognitionConfig(
            config=speech_v1.RecognitionConfig(
                encoding=speech_v1.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=settings.audio_sample_rate,
                language_code=settings.google_stt_language_code,
                max_alternatives=1,
            ),
            interim_results=True,
        )
        # 各APIリクエストから共有する。更新時に元の音声iteratorを閉じない。
        audio = aiter(audio_chunks)
        pending = None
        exhausted = False
        segment_start_ts = time.monotonic()
        current_segment_id = str(uuid.uuid4())

        try:
            while not exhausted:
                started = time.monotonic()
                audio_seconds = 0.0
                detector = SilenceDetector(settings.audio_sample_rate, self.vad_threshold_dbfs,
                                           self.SILENCE_SECONDS, self.MIN_STREAM_SECONDS)

                async def requests():
                    nonlocal pending, exhausted, audio_seconds
                    yield speech_v1.StreamingRecognizeRequest(streaming_config=streaming_config)
                    while True:
                        # タイムアウト時にも取得中のチャンクをキャンセルせず次の接続へ渡す。
                        if pending is None:
                            pending = asyncio.create_task(anext(audio))
                        remaining = self.STREAM_LIMIT_SECONDS - (time.monotonic() - started)
                        done, _ = await asyncio.wait({pending}, timeout=max(0, remaining))
                        if not done:
                            logger.info("Google STT stream rotated: time limit")
                            return
                        try:
                            chunk = pending.result()
                        except StopAsyncIteration:
                            exhausted = True
                            return
                        finally:
                            pending = None
                        duration = len(chunk) / (2 * settings.audio_sample_rate)
                        audio_seconds += duration
                        boundary = detector.push(chunk)
                        # 無音も送信し、Googleの最終結果を受け取ってから次の接続を開く。
                        yield speech_v1.StreamingRecognizeRequest(audio_content=chunk)
                        if boundary:
                            logger.info("Google STT stream rotated: silence")
                            return
                        if (audio_seconds >= self.STREAM_LIMIT_SECONDS
                                or time.monotonic() - started >= self.STREAM_LIMIT_SECONDS):
                            logger.info("Google STT stream rotated: time limit")
                            return

                responses = await self._client.streaming_recognize(requests=requests())
                async for response in responses:
                    for result in response.results:
                        if not result.alternatives or not result.alternatives[0].transcript:
                            continue
                        yield TranscriptEvent(
                            segment_id=current_segment_id,
                            audio_source="microphone",
                            text=result.alternatives[0].transcript,
                            is_final=result.is_final,
                            start_ts=segment_start_ts,
                            end_ts=time.monotonic(),
                        )
                        if result.is_final:
                            current_segment_id = str(uuid.uuid4())
                            segment_start_ts = time.monotonic()
                # 確定結果を受け取らずに終了した接続でも次の発話と同じIDにしない。
                current_segment_id = str(uuid.uuid4())
                segment_start_ts = time.monotonic()
        finally:
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
