import asyncio
import base64
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.schemas import StartSessionMessage, TranscriptEvent
from app.stt import get_stt_provider
from app.stt.vad import SilenceDetector, SilenceSegmenter
from app.stt.openai_realtime import OpenAiRealtimeSttProvider
from app.stt.whisper_cpp import WhisperCppSttProvider


def pcm(level, seconds=0.1):
    return struct.pack('<h', level) * round(settings.audio_sample_rate * seconds)


async def audio(chunks):
    for chunk in chunks:
        yield chunk


def test_shared_detector_uses_audio_duration_and_resets_on_voice():
    vad = SilenceDetector(16000, -45)
    assert not vad.push(pcm(5000))
    assert not vad.push(pcm(0, 0.7))
    assert not vad.push(pcm(5000))
    assert not vad.push(pcm(0, 0.7))
    assert vad.push(pcm(0))
    vad.reset()
    assert not vad.push(pcm(0, 2))


def test_segmenter_preserves_speech_and_trailing_audio_with_bounded_buffers():
    segmenter = SilenceSegmenter(16000)
    assert segmenter.push(pcm(0, 10)) is None
    voice = pcm(5000)
    assert segmenter.push(voice) is None
    assert segmenter.push(pcm(0, 0.8)) == voice + pcm(0, 0.8)
    assert segmenter.flush_remaining() is None
    segmenter = SilenceSegmenter(16000, max_seconds=0.2)
    assert segmenter.push(voice) is None
    assert segmenter.push(voice) == voice * 2
    assert segmenter.push(voice) is None
    assert segmenter.flush_remaining() == voice


@pytest.mark.parametrize('name,module,cls', [
    ('cloud_google', 'google_cloud', 'GoogleCloudSttProvider'),
    ('cloud_openai', 'openai_realtime', 'OpenAiRealtimeSttProvider'),
    ('local_whispercpp', 'whisper_cpp', 'WhisperCppSttProvider'),
    ('mock', 'mock', 'MockSttProvider'),
])
def test_factory_passes_common_threshold_to_every_provider(monkeypatch, name, module, cls):
    monkeypatch.setattr(f'app.stt.{module}.{cls}', lambda **kw: SimpleNamespace(**kw))
    # Mock is imported at factory module scope.
    if name == 'mock':
        monkeypatch.setattr('app.stt.MockSttProvider', lambda **kw: SimpleNamespace(**kw))
    assert get_stt_provider(name, vad_threshold_dbfs=-30).vad_threshold_dbfs == -30


@pytest.mark.asyncio
async def test_whisper_uses_common_threshold_and_flushes_last_utterance(monkeypatch):
    monkeypatch.setattr('app.stt.whisper_cpp._resolve_binary', lambda: Path('/fake'))
    monkeypatch.setattr('app.stt.whisper_cpp._resolve_model_path', lambda: Path('/fake'))
    chunks = [pcm(1000), pcm(0, 0.8), pcm(2000)]
    received = []
    stt = WhisperCppSttProvider(vad_threshold_dbfs=-40)

    async def transcribe(chunk):
        received.append(chunk)
        return TranscriptEvent(segment_id=str(len(received)), audio_source='microphone',
                               text='test', is_final=True, start_ts=0, end_ts=1)

    stt._transcribe_segment = transcribe
    events = [e async for e in stt.stream_transcribe(audio(chunks))]
    assert len(events) == 2
    assert received == [chunks[0] + chunks[1], chunks[2]]
    stt = WhisperCppSttProvider(vad_threshold_dbfs=-20)
    stt._transcribe_segment = transcribe
    assert not [e async for e in stt.stream_transcribe(audio(chunks))]


@pytest.mark.asyncio
async def test_mock_uses_common_threshold():
    chunks = [pcm(1000), pcm(0, 0.8), pcm(2000)]
    stt = get_stt_provider('mock', vad_threshold_dbfs=-40)
    assert len([e async for e in stt.stream_transcribe(audio(chunks))]) == 2
    stt = get_stt_provider('mock', vad_threshold_dbfs=-20)
    assert not [e async for e in stt.stream_transcribe(audio(chunks))]


class Socket:
    def __init__(self, stt):
        self.stt = stt
        self.messages = []
        self.closed = False

    async def send(self, text):
        message = json.loads(text)
        self.messages.append(message)
        if message['type'] == 'input_audio_buffer.commit':
            # Simulate completion reception while sender is active.
            self.stt._pending_commits -= 1
            self.stt._all_completed.set()

    async def close(self):
        self.closed = True


def openai(monkeypatch, threshold=-45):
    monkeypatch.setattr(settings, 'openai_api_key', 'test')
    return OpenAiRealtimeSttProvider(vad_threshold_dbfs=threshold)


@pytest.mark.asyncio
async def test_openai_commits_on_shared_vad_and_flushes_short_tail(monkeypatch):
    stt = openai(monkeypatch)
    ws = Socket(stt)
    assert stt._build_session_update()['session']['audio']['input']['turn_detection'] is None
    chunks = [pcm(0, 1), pcm(5000), pcm(0, 0.8), pcm(5000, 0.02)]
    await stt._send_audio(ws, audio(chunks))
    assert [m['type'] for m in ws.messages] == [
        'input_audio_buffer.append', 'input_audio_buffer.append', 'input_audio_buffer.commit',
        'input_audio_buffer.append', 'input_audio_buffer.append', 'input_audio_buffer.commit',
    ]
    assert sum(len(base64.b64decode(m['audio'])) for m in ws.messages[-3:] if 'audio' in m) >= 4800
    assert ws.closed


@pytest.mark.asyncio
async def test_openai_skips_silence_and_applies_threshold(monkeypatch):
    stt = openai(monkeypatch, -20)
    ws = Socket(stt)
    await stt._send_audio(ws, audio([pcm(1000), pcm(0, 0.8)]))
    assert not ws.messages
    assert ws.closed


@pytest.mark.asyncio
async def test_openai_long_speech_is_committed(monkeypatch):
    stt = openai(monkeypatch)
    ws = Socket(stt)
    await stt._send_audio(ws, audio([pcm(5000, 10)] * 4))
    assert len([m for m in ws.messages if m['type'] == 'input_audio_buffer.commit']) == 2


@pytest.mark.asyncio
async def test_openai_keeps_interleaved_items_separate(monkeypatch):
    stt = openai(monkeypatch)
    stt._pending_commits = 2
    stt._all_completed.clear()

    async def messages():
        for item, suffix, text in [('a', 'delta', 'A'), ('b', 'delta', 'B'),
                                   ('b', 'completed', 'BB'), ('a', 'completed', 'AA')]:
            yield json.dumps({'type': f'conversation.item.input_audio_transcription.{suffix}',
                              'item_id': item, 'delta': text, 'transcript': text})

    events = [e async for e in stt._receive_events(messages())]
    assert events[0].segment_id == events[3].segment_id
    assert events[1].segment_id == events[2].segment_id
    assert events[0].segment_id != events[1].segment_id
    assert [e.text for e in events] == ['A', 'B', 'BB', 'AA']
    assert stt._all_completed.is_set()


def test_old_google_field_remains_compatible_but_new_name_serializes():
    message = StartSessionMessage(google_vad_threshold_dbfs=-30)
    assert message.vad_threshold_dbfs == -30
    assert message.model_dump()['vad_threshold_dbfs'] == -30
