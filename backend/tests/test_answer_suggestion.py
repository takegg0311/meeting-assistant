"""回答提案(Issue #2)の核心ロジック: grace period とコンテキスト組み立ての検証。"""

import asyncio
import time
import uuid

import pytest

from app.config import settings
from app.llm.base import AnswerSuggestionContext
from app.schemas import TranscriptEvent
from app.session.meeting_session import MeetingSession

pytestmark = pytest.mark.asyncio


class FakeWebSocket:
    """send_json された内容を記録するだけのWebSocketスタブ。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    def events(self, event_type: str) -> list[dict]:
        return [p for p in self.sent if p["type"] == event_type]


class FakeLlmProvider:
    """呼び出されたコンテキストを記録するLLMスタブ。"""

    def __init__(self, answer: str = "ダミー回答", delay: float = 0.0) -> None:
        self.answer = answer
        self.delay = delay
        self.calls: list[AnswerSuggestionContext] = []

    async def generate_answer_suggestion(self, context: AnswerSuggestionContext) -> str:
        self.calls.append(context)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.answer


class FailingLlmProvider:
    async def generate_answer_suggestion(self, context: AnswerSuggestionContext) -> str:
        raise RuntimeError("LLM unavailable")


def make_session(llm_provider) -> tuple[MeetingSession, FakeWebSocket]:
    websocket = FakeWebSocket()
    session = MeetingSession(
        websocket=websocket,  # type: ignore[arg-type]
        stt_provider_name="mock",
        audio_source="microphone",
    )
    session._llm_provider = llm_provider  # type: ignore[assignment]
    return session, websocket


def make_segment(text: str, is_final: bool = True) -> TranscriptEvent:
    now = time.monotonic()
    return TranscriptEvent(
        segment_id=str(uuid.uuid4()),
        audio_source="microphone",
        text=text,
        is_final=is_final,
        start_ts=now,
        end_ts=now,
    )


async def drain(session: MeetingSession) -> None:
    """起動済みの回答提案タスクの完了を待つ。"""
    if session._answer_tasks:
        await asyncio.gather(*session._answer_tasks, return_exceptions=True)


async def test_generating_event_is_sent_before_waiting(monkeypatch):
    """押下直後に生成中カードを出すため、grace period 前に generating を配信する。"""
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 50)
    llm = FakeLlmProvider()
    session, ws = make_session(llm)
    await session.on_final_segment(make_segment("コストはどうなりますか？"))

    session.request_answer_suggestion("req-1")
    await asyncio.sleep(0)  # タスクを起動させるが、grace period は完了させない

    statuses = [e["status"] for e in ws.events("answer_suggestion")]
    assert statuses == ["generating"]
    assert llm.calls == [], "grace period 前にLLMを呼んではいけない"

    await drain(session)


async def test_segment_finalized_after_press_is_included(monkeypatch):
    """押下時点で未確定だった質問末尾が、grace period 中に確定すれば対象に含まれる。"""
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 200)
    llm = FakeLlmProvider()
    session, ws = make_session(llm)
    await session.on_final_segment(make_segment("新しい構成について説明します。"))

    session.request_answer_suggestion("req-1")
    await asyncio.sleep(0.05)
    # 押下後に質問末尾が確定する(VADの無音待ち + STT処理の遅延を再現)
    await session.on_final_segment(make_segment("この方式のコストはどうなりますか？"))

    await drain(session)

    assert len(llm.calls) == 1
    texts = [t.text for t in llm.calls[0].question_turns]
    assert "この方式のコストはどうなりますか？" in texts, "押下後に確定した発話が含まれていない"


async def test_grace_period_waits_once_when_silent(monkeypatch):
    """無音時は猶予時間を1回分だけ待ち、延長上限までは待たない。"""
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 150)
    llm = FakeLlmProvider()
    session, ws = make_session(llm)
    await session.on_final_segment(make_segment("ご質問はありますか？"))

    started = time.monotonic()
    session.request_answer_suggestion("req-1")
    await drain(session)
    elapsed = time.monotonic() - started

    # 猶予1回分(0.15s)は待つが、延長上限(0.45s)までは待たない。
    assert 0.15 <= elapsed < 0.4, f"elapsed={elapsed}"


async def test_grace_period_ends_as_soon_as_segment_arrives(monkeypatch):
    """猶予時間内にセグメントが確定したら待ち直さず即座に生成へ進む。

    待ち直すと発話が途切れない限り生成が始まらず、2〜3秒の目標を超えるため。
    """
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 1000)
    llm = FakeLlmProvider()
    session, _ = make_session(llm)
    await session.on_final_segment(make_segment("最初の発話"))

    async def keep_speaking() -> None:
        # 猶予より短い間隔でセグメントを確定させ続ける(発話継続を再現)。
        for i in range(20):
            await asyncio.sleep(0.05)
            await session.on_final_segment(make_segment(f"継続{i}"))

    speaker = asyncio.create_task(keep_speaking())
    started = time.monotonic()
    session.request_answer_suggestion("req-1")
    await drain(session)
    elapsed = time.monotonic() - started
    speaker.cancel()

    assert len(llm.calls) == 1
    # 最初の確定(約0.05s)で打ち切られ、猶予(1.0s)を待たない。
    assert elapsed < 0.5, f"elapsed={elapsed}"


async def test_question_and_context_are_split(monkeypatch):
    """直近セグメントを質問候補、それ以前を文脈として分けて渡す。"""
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 0)
    monkeypatch.setattr(settings, "answer_suggestion_question_segments", 2)
    llm = FakeLlmProvider()
    session, _ = make_session(llm)

    for text in ["一つ目", "二つ目", "三つ目", "四つ目"]:
        await session.on_final_segment(make_segment(text))

    session.request_answer_suggestion("req-1")
    await drain(session)

    context = llm.calls[0]
    assert [t.text for t in context.question_turns] == ["三つ目", "四つ目"]
    assert [t.text for t in context.context_turns] == ["一つ目", "二つ目"]


async def test_interim_segments_are_excluded(monkeypatch):
    """未確定セグメントはコンテキストに含めない(確定transcriptのみを根拠にする)。"""
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 0)
    llm = FakeLlmProvider()
    session, _ = make_session(llm)

    await session.on_final_segment(make_segment("確定した発話"))
    await session.on_final_segment(make_segment("未確定の発話", is_final=False))

    session.request_answer_suggestion("req-1")
    await drain(session)

    all_texts = [t.text for t in llm.calls[0].question_turns + llm.calls[0].context_turns]
    assert all_texts == ["確定した発話"]


async def test_done_event_carries_answer_and_sources(monkeypatch):
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 0)
    llm = FakeLlmProvider(answer="コストは月あたり約10万円の見込みです。")
    session, ws = make_session(llm)
    segment = make_segment("コストはどうなりますか？")
    await session.on_final_segment(segment)

    session.request_answer_suggestion("req-1")
    await drain(session)

    done = [e for e in ws.events("answer_suggestion") if e["status"] == "done"]
    assert len(done) == 1
    assert done[0]["request_id"] == "req-1"
    assert done[0]["answer"] == "コストは月あたり約10万円の見込みです。"
    assert done[0]["source_segment_ids"] == [segment.segment_id]


async def test_error_when_no_transcript_yet(monkeypatch):
    """文字起こしがまだ無い状態で押された場合はエラーを返し、LLMを呼ばない。"""
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 0)
    llm = FakeLlmProvider()
    session, ws = make_session(llm)

    session.request_answer_suggestion("req-1")
    await drain(session)

    errors = [e for e in ws.events("answer_suggestion") if e["status"] == "error"]
    assert len(errors) == 1
    assert llm.calls == []


async def test_llm_failure_does_not_break_transcription(monkeypatch):
    """LLM失敗はerrorイベントに留め、文字起こしの配信は継続する。"""
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 0)
    session, ws = make_session(FailingLlmProvider())
    await session.on_final_segment(make_segment("質問です"))

    session.request_answer_suggestion("req-1")
    await drain(session)

    errors = [e for e in ws.events("answer_suggestion") if e["status"] == "error"]
    assert len(errors) == 1

    # 失敗後もtranscript配信が続く
    await session.on_final_segment(make_segment("次の発話"))
    assert ws.events("transcript")[-1]["text"] == "次の発話"


async def test_llm_init_failure_does_not_block_session(monkeypatch):
    """LLMプロバイダの初期化失敗(APIキー未設定など)で文字起こしを止めない。

    LLMは初回リクエスト時に生成するため、セッション開始と文字起こしは成功し、
    回答提案だけが error になる。
    """
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 0)
    monkeypatch.setattr(settings, "llm_provider", "cloud_anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "")  # 初期化を失敗させる

    websocket = FakeWebSocket()
    # プロバイダを差し替えず、実際の遅延初期化パスを通す。
    session = MeetingSession(
        websocket=websocket,  # type: ignore[arg-type]
        stt_provider_name="mock",
        audio_source="microphone",
    )

    # セッション生成と文字起こしはLLMに依存せず成功する。
    await session.on_final_segment(make_segment("質問です"))
    assert websocket.events("transcript")[-1]["text"] == "質問です"

    session.request_answer_suggestion("req-1")
    await drain(session)

    errors = [e for e in websocket.events("answer_suggestion") if e["status"] == "error"]
    assert len(errors) == 1

    # 失敗後も文字起こしは継続する
    await session.on_final_segment(make_segment("次の発話"))
    assert websocket.events("transcript")[-1]["text"] == "次の発話"


async def test_concurrent_requests_are_tracked_independently(monkeypatch):
    """連打時は複数リクエストが並行して走り、それぞれのrequest_idで結果が返る。"""
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 0)
    monkeypatch.setattr(settings, "answer_suggestion_max_concurrency", 3)
    llm = FakeLlmProvider(delay=0.05)
    session, ws = make_session(llm)
    await session.on_final_segment(make_segment("質問です"))

    for request_id in ("req-1", "req-2", "req-3"):
        session.request_answer_suggestion(request_id)
    await drain(session)

    done = [e for e in ws.events("answer_suggestion") if e["status"] == "done"]
    assert {e["request_id"] for e in done} == {"req-1", "req-2", "req-3"}
