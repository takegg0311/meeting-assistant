import asyncio

import pytest

from app.session.meeting_session import MeetingSession, _to_turn


class Socket:
    def __init__(self):
        self.events = []

    async def send_json(self, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_dual_audio_is_routed_and_labeled():
    socket = Socket()
    session = MeetingSession(socket, "mock", "tab_audio", ["tab_audio", "microphone"])
    await session.start()
    # Each source remains in speech until its own silence boundary arrives.
    for _ in range(19):
        await session.push_audio_chunk(b"\x00" + b"\x00\x20" * 160)
        await session.push_audio_chunk(b"\x01" + b"\x00\x20" * 160)
    await asyncio.sleep(0)
    assert not [e for e in socket.events if e["type"] == "transcript"]
    await session.push_audio_chunk(b"\x00" + b"\x00\x20" * 160)
    await session.push_audio_chunk(b"\x01" + b"\x00\x20" * 160)
    await session.push_audio_chunk(b"\x00" + bytes(25600))
    await session.push_audio_chunk(b"\x01" + bytes(25600))
    await session.stop()
    transcripts = [e for e in socket.events if e["type"] == "transcript"]
    assert len(transcripts) == 2
    assert {(e["audio_source"], e["speaker_id"]) for e in transcripts} == {
        ("tab_audio", "相手"), ("microphone", "自分")
    }
    assert len({e["segment_id"] for e in transcripts}) == 2
    assert {_to_turn(e).speaker_id for e in session._final_segments} == {"自分", "相手"}
    assert socket.events[-1]["stage"] == "session_stopped"


@pytest.mark.asyncio
async def test_invalid_frame_does_not_kill_session():
    socket = Socket()
    session = MeetingSession(socket, "mock", "system_audio", ["system_audio", "microphone"])
    await session.start()
    await session.push_audio_chunk(b"")
    await session.push_audio_chunk(b"\x02invalid")
    for _ in range(20):
        await session.push_audio_chunk(b"\x01" + b"\x00\x20" * 160)
    await session.stop()
    assert len([e for e in socket.events if e["type"] == "transcript"]) == 1
    assert len([e for e in socket.events if e.get("stage") == "error"]) == 2


@pytest.mark.asyncio
async def test_legacy_single_source_accepts_unframed_pcm():
    socket = Socket()
    session = MeetingSession(socket, "mock", "microphone")
    await session.start()
    for _ in range(20):
        await session.push_audio_chunk(b"\x00\x01")
    await session.stop()
    assert len([e for e in socket.events if e["type"] == "transcript"]) == 1
