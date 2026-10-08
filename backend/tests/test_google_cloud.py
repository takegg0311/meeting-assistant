import asyncio
import struct
from types import SimpleNamespace

import pytest

from app.schemas import StartSessionMessage
from app.stt.google_cloud import GoogleCloudSttProvider
from app.stt.vad import pcm_level_dbfs


def pcm(level, seconds=0.1):
    return struct.pack('<h', level) * round(16000 * seconds)


class FakeClient:
    def __init__(self):
        self.streams = []

    async def streaming_recognize(self, *, requests):
        async def responses():
            chunks = []
            self.streams.append(chunks)
            async for request in requests:
                if request.audio_content:
                    chunks.append(request.audio_content)
            yield SimpleNamespace(results=[SimpleNamespace(
                alternatives=[SimpleNamespace(transcript=f'stream {len(self.streams)}')],
                is_final=True,
            )])
        return responses()


def provider(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr('app.stt.google_cloud.speech_v1.SpeechAsyncClient', lambda: client)
    stt = GoogleCloudSttProvider()
    stt.MIN_STREAM_SECONDS = 0
    return stt, client


async def audio(chunks):
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
async def test_silence_rotates_without_losing_audio_or_final_results(monkeypatch):
    stt, client = provider(monkeypatch)
    chunks = [pcm(5000)] + [pcm(0)] * 9 + [pcm(6000)]
    events = [event async for event in stt.stream_transcribe(audio(chunks))]
    assert len(client.streams) == 2
    assert [chunk for stream in client.streams for chunk in stream] == chunks
    assert all(event.is_final for event in events)
    assert len({event.segment_id for event in events}) == 2


@pytest.mark.asyncio
async def test_continuous_speech_hits_audio_duration_fallback(monkeypatch):
    stt, client = provider(monkeypatch)
    stt.STREAM_LIMIT_SECONDS = 0.25
    chunks = [pcm(5000)] * 7
    _ = [event async for event in stt.stream_transcribe(audio(chunks))]
    assert len(client.streams) == 3
    assert [chunk for stream in client.streams for chunk in stream] == chunks


@pytest.mark.asyncio
async def test_wall_timeout_preserves_pending_audio(monkeypatch):
    stt, client = provider(monkeypatch)
    stt.STREAM_LIMIT_SECONDS = 0.02
    chunks = [pcm(5000, 0.001), pcm(6000, 0.001)]

    async def delayed_audio():
        yield chunks[0]
        await asyncio.sleep(0.035)
        yield chunks[1]

    _ = [event async for event in stt.stream_transcribe(delayed_audio())]
    assert len(client.streams) == 2
    assert [chunk for stream in client.streams for chunk in stream] == chunks


@pytest.mark.asyncio
async def test_silence_only_does_not_repeatedly_rotate(monkeypatch):
    stt, client = provider(monkeypatch)
    _ = [event async for event in stt.stream_transcribe(audio([pcm(0)] * 20))]
    assert len(client.streams) == 1


@pytest.mark.asyncio
async def test_threshold_changes_silence_detection(monkeypatch):
    stt, client = provider(monkeypatch)
    stt.vad_threshold_dbfs = -20
    chunks = [pcm(16000)] + [pcm(1000)] * 9 + [pcm(16000)]
    _ = [event async for event in stt.stream_transcribe(audio(chunks))]
    assert len(client.streams) == 2
    stt, client = provider(monkeypatch)
    stt.vad_threshold_dbfs = -60
    _ = [event async for event in stt.stream_transcribe(audio(chunks))]
    assert len(client.streams) == 1


def test_pcm_level_and_schema_bounds():
    assert pcm_level_dbfs(pcm(32767)) == pytest.approx(0, abs=0.001)
    assert pcm_level_dbfs(pcm(3277)) == pytest.approx(-20, abs=0.001)
    assert pcm_level_dbfs(pcm(0)) == float('-inf')
    with pytest.raises(ValueError):
        StartSessionMessage(vad_threshold_dbfs=-100)


@pytest.mark.asyncio
async def test_minimum_duration_prevents_short_pause_churn(monkeypatch):
    stt, client = provider(monkeypatch)
    stt.MIN_STREAM_SECONDS = 10
    _ = [event async for event in stt.stream_transcribe(audio([pcm(5000)] + [pcm(0)] * 20))]
    assert len(client.streams) == 1


@pytest.mark.asyncio
async def test_cancellation_cleans_up_pending_read(monkeypatch):
    stt, client = provider(monkeypatch)
    reading = asyncio.Event()
    closed = asyncio.Event()

    async def waiting_audio():
        try:
            reading.set()
            await asyncio.Event().wait()
            yield pcm(1000)
        finally:
            closed.set()

    async def consume():
        return [event async for event in stt.stream_transcribe(waiting_audio())]

    task = asyncio.create_task(consume())
    await reading.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
