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
    """send_json された内容を記録するWebSocketスタブ。

    `block_on` を指定すると、その種別のイベント送信をイベント待ちで止められる。
    `fail_from` を指定すると、その回数目以降の送信を切断相当で失敗させる。
    """

    def __init__(self, block_on: str | None = None, fail_from: int | None = None) -> None:
        self.sent: list[dict] = []
        self.block_on = block_on
        self.fail_from = fail_from
        self.release = asyncio.Event()
        self._attempts = 0

    async def send_json(self, payload: dict) -> None:
        self._attempts += 1
        if self.fail_from is not None and self._attempts >= self.fail_from:
            raise RuntimeError("WebSocket is closed")
        if self.block_on is not None and payload["type"] == self.block_on:
            await self.release.wait()
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


def make_session(llm_provider, websocket: "FakeWebSocket | None" = None):
    """送信タスクを起動した状態のセッションを返す。

    送信はキュー経由の単一タスクが行うため、起動しないとイベントが記録されない。
    """
    websocket = websocket or FakeWebSocket()
    session = MeetingSession(
        websocket=websocket,  # type: ignore[arg-type]
        stt_provider_name="mock",
        audio_source="microphone",
    )
    session._llm_provider = llm_provider  # type: ignore[assignment]
    session._sender_task = asyncio.create_task(session._run_sender_loop())
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
    """回答提案タスクの完了と、送信キューの吐き出しを待つ。"""
    if session._answer_tasks:
        await asyncio.gather(*session._answer_tasks, return_exceptions=True)
    await flush(session)


async def flush(session: MeetingSession, timeout: float = 1.0) -> None:
    """投入済みのイベントが送信タスクに処理されるまで待つ。

    送信が意図的に詰まらせてあるテストでは待ち切れないため、タイムアウトで諦める
    (詰まっていること自体が検証対象なので、ここで失敗させない)。
    """
    try:
        await asyncio.wait_for(session._send_queue.join(), timeout=timeout)
    except asyncio.TimeoutError:
        pass


async def test_generating_event_is_sent_before_waiting(monkeypatch):
    """押下直後に生成中カードを出すため、grace period 前に generating を配信する。"""
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 50)
    llm = FakeLlmProvider()
    session, ws = make_session(llm)
    await session.on_final_segment(make_segment("コストはどうなりますか？"))

    session.request_answer_suggestion("req-1")
    await asyncio.sleep(0)  # 生成タスクを起動し generating を投入させる
    await flush(session)  # 送信まで進めるが、grace period は完了させない

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
    await flush(session)
    assert ws.events("transcript")[-1]["text"] == "次の発話"


async def test_llm_init_failure_does_not_block_session(monkeypatch):
    """LLMプロバイダの初期化失敗(APIキー未設定など)で文字起こしを止めない。

    LLMは初回リクエスト時に生成するため、セッション開始と文字起こしは成功し、
    回答提案だけが error になる。
    """
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 0)
    monkeypatch.setattr(settings, "llm_provider", "cloud_anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "")  # 初期化を失敗させる

    # プロバイダを差し替えず、実際の遅延初期化パスを通す。
    session, websocket = make_session(None)
    session._llm_provider = None  # type: ignore[assignment]

    # セッション生成と文字起こしはLLMに依存せず成功する。
    await session.on_final_segment(make_segment("質問です"))
    await flush(session)
    assert websocket.events("transcript")[-1]["text"] == "質問です"

    session.request_answer_suggestion("req-1")
    await drain(session)

    errors = [e for e in websocket.events("answer_suggestion") if e["status"] == "error"]
    assert len(errors) == 1

    # 失敗後も文字起こしは継続する
    await session.on_final_segment(make_segment("次の発話"))
    await flush(session)
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


# --- 以下、Cursorレビュー指摘に対する回帰テスト ---


async def test_answer_send_does_not_block_transcript(monkeypatch):
    """回答提案の送信が詰まっても、文字起こし(優先度1)の配信は待たされない。

    このプロジェクトの根幹方針(優先度1を絶対にブロックしない)の回帰テスト。
    送信を単一タスク + キューに分離する前は、共有ロックで transcript が止まっていた。
    """
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 0)
    websocket = FakeWebSocket(block_on="answer_suggestion")
    session, _ = make_session(FakeLlmProvider(), websocket)

    session.request_answer_suggestion("req-1")
    await asyncio.sleep(0)  # generating の送信がブロックに入る

    # 送信が詰まっている状態でも on_final_segment は返る
    await asyncio.wait_for(session.on_final_segment(make_segment("優先度1の発話")), timeout=0.5)

    websocket.release.set()
    await drain(session)
    assert websocket.events("transcript")[-1]["text"] == "優先度1の発話"


async def test_grace_uses_count_at_request_time(monkeypatch):
    """grace period の基準は押下を受け取った時点の確定数。

    基準をタスク開始後に取ると、押下からタスク実行までに確定したセグメントを
    到着済みと誤認し、猶予を満額待ってしまう(修正前は満額待機していた)。
    """
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 300)
    llm = FakeLlmProvider()
    session, _ = make_session(llm)
    await session.on_final_segment(make_segment("最初の発話"))

    started = time.monotonic()
    session.request_answer_suggestion("req-1")
    # タスクが grace に入る前にセグメントが確定する状況(STTが先に走るケース)
    await session.on_final_segment(make_segment("押下直後に確定した質問末尾"))
    await drain(session)
    elapsed = time.monotonic() - started

    texts = [t.text for t in llm.calls[0].question_turns]
    assert "押下直後に確定した質問末尾" in texts
    assert elapsed < 0.25, f"猶予を満額待っている: elapsed={elapsed}"


async def test_history_updated_before_send(monkeypatch):
    """確定セグメントの履歴更新は送信より先に行う。

    送信が滞っても、待機中の回答提案が質問末尾を取りこぼさないため。
    """
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 300)
    llm = FakeLlmProvider()
    websocket = FakeWebSocket(block_on="transcript")
    session, _ = make_session(llm, websocket)

    session.request_answer_suggestion("req-1")
    await asyncio.sleep(0)
    # transcript の送信は詰まるが、履歴とカウンタは先に更新される
    await session.on_final_segment(make_segment("送信が滞る質問末尾"))
    await drain(session)

    texts = [t.text for t in llm.calls[0].question_turns]
    assert "送信が滞る質問末尾" in texts


async def test_stop_bounds_wait_and_cancels_slow_generation(monkeypatch):
    """セッション終了時、生成中の回答提案を無期限には待たない。

    クライアントは session_stopped を数秒で待つのをやめるため、それを超えて
    待っても結果は届かない。期限を超えた生成はキャンセルする。
    """
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 0)
    monkeypatch.setattr(settings, "answer_suggestion_stop_grace_ms", 100)
    llm = FakeLlmProvider(delay=5.0)  # 終わらない生成
    session, websocket = make_session(llm)
    await session.on_final_segment(make_segment("質問です"))

    session.request_answer_suggestion("req-1")
    await asyncio.sleep(0.02)

    started = time.monotonic()
    await session.stop()
    elapsed = time.monotonic() - started

    assert elapsed < 1.0, f"停止が生成待ちで長引いている: elapsed={elapsed}"
    assert websocket.events("status")[-1]["stage"] == "session_stopped"
    assert not [t for t in session._answer_tasks if not t.done()]


async def test_stop_without_notify_cancels_and_sends_nothing(monkeypatch):
    """切断済みなら生成を待たず、クライアントへの送信も行わない。"""
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 0)
    llm = FakeLlmProvider(delay=5.0)
    session, websocket = make_session(llm)
    await session.on_final_segment(make_segment("質問です"))

    session.request_answer_suggestion("req-1")
    await asyncio.sleep(0.02)
    sent_before = len(websocket.sent)

    started = time.monotonic()
    await session.stop(notify_client=False)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert len(websocket.sent) == sent_before, "切断後に送信している"
    assert not [e for e in websocket.events("status") if e["stage"] == "session_stopped"]


async def test_llm_timeout_falls_back_to_error(monkeypatch):
    """LLMが返らない場合も generating のまま放置せず error へ落とす。

    完了条件「LLM呼び出しが失敗しても…カードにエラー表示のみが出る」は、
    例外だけでなくハングにも当てはまる必要がある。
    """
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 0)
    monkeypatch.setattr(settings, "answer_suggestion_timeout_ms", 80)
    session, websocket = make_session(FakeLlmProvider(delay=5.0))
    await session.on_final_segment(make_segment("質問です"))

    session.request_answer_suggestion("req-1")
    await drain(session)

    errors = [e for e in websocket.events("answer_suggestion") if e["status"] == "error"]
    assert len(errors) == 1
    assert "時間内" in errors[0]["message"]

    # ハング後も文字起こしは継続する
    await session.on_final_segment(make_segment("次の発話"))
    await flush(session)
    assert websocket.events("transcript")[-1]["text"] == "次の発話"


async def test_send_failure_does_not_stop_transcription(monkeypatch):
    """送信が切断で失敗しても、以降の on_final_segment は例外を投げない。"""
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 0)
    websocket = FakeWebSocket(fail_from=1)
    session, _ = make_session(FakeLlmProvider(), websocket)

    await session.on_final_segment(make_segment("送信に失敗する発話"))
    await flush(session)
    # 失敗後も呼び出し側へ例外は伝わらない
    await session.on_final_segment(make_segment("その後の発話"))
    await flush(session)

    assert session._send_failed is True


async def test_semaphore_limits_peak_concurrency(monkeypatch):
    """同時実行数の上限が実際に効いている(上限を超える件数で同時実行数を測る)。"""
    monkeypatch.setattr(settings, "answer_suggestion_grace_ms", 0)
    monkeypatch.setattr(settings, "answer_suggestion_max_concurrency", 2)

    peak = 0
    running = 0

    class CountingLlmProvider:
        async def generate_answer_suggestion(self, context):
            nonlocal peak, running
            running += 1
            peak = max(peak, running)
            try:
                await asyncio.sleep(0.05)
                return "x"
            finally:
                running -= 1

    session, websocket = make_session(CountingLlmProvider())
    await session.on_final_segment(make_segment("質問です"))

    for i in range(6):
        session.request_answer_suggestion(f"req-{i}")
    await drain(session)

    assert peak <= 2, f"同時実行数の上限を超えている: peak={peak}"
    done = [e for e in websocket.events("answer_suggestion") if e["status"] == "done"]
    assert len(done) == 6
