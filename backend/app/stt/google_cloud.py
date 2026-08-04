import logging
import time
import uuid
from typing import AsyncIterator

from google.cloud import speech_v1

from app.config import settings
from app.schemas import TranscriptEvent

logger = logging.getLogger(__name__)


class GoogleCloudSttProvider:
    """Google Cloud Speech-to-TextのStreamingRecognize(gRPC双方向ストリーミング)を使うクラウドSTT実装。"""

    def __init__(self) -> None:
        self._client = speech_v1.SpeechAsyncClient()

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

        responses = await self._client.streaming_recognize(
            requests=self._build_requests(streaming_config, audio_chunks)
        )

        segment_start_ts = time.monotonic()
        current_segment_id = str(uuid.uuid4())

        async for response in responses:
            if not response.results:
                continue
            result = response.results[0]
            if not result.alternatives:
                continue

            transcript = result.alternatives[0].transcript
            if not transcript:
                continue

            yield TranscriptEvent(
                segment_id=current_segment_id,
                audio_source="microphone",
                text=transcript,
                is_final=result.is_final,
                start_ts=segment_start_ts,
                end_ts=time.monotonic(),
            )

            if result.is_final:
                current_segment_id = str(uuid.uuid4())
                segment_start_ts = time.monotonic()

    async def _build_requests(
        self,
        streaming_config: speech_v1.StreamingRecognitionConfig,
        audio_chunks: AsyncIterator[bytes],
    ):
        yield speech_v1.StreamingRecognizeRequest(streaming_config=streaming_config)
        async for chunk in audio_chunks:
            yield speech_v1.StreamingRecognizeRequest(audio_content=chunk)
