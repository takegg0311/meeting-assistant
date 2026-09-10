import asyncio
import logging
from collections import deque
from typing import AsyncIterator

from fastapi import WebSocket

from app.config import settings
from app.llm import AnswerSuggestionContext, ConversationTurn, LlmProvider, get_llm_provider
from app.schemas import AnswerSuggestionEvent, AudioSource, StatusEvent, TranscriptEvent
from app.stt import get_stt_provider

logger = logging.getLogger(__name__)

_QUEUE_DONE = object()


class MeetingSession:
    """WebSocket接続ごとに1つ保持し、音声受信→STT→transcript配信を仲介する。"""

    def __init__(self, websocket: WebSocket, stt_provider_name: str, audio_source: AudioSource):
        self._websocket = websocket
        self._audio_source = audio_source
        self._stt_provider = get_stt_provider(stt_provider_name)
        self._audio_queue: asyncio.Queue[bytes | object] = asyncio.Queue()
        self._stt_task: asyncio.Task | None = None

        # 送信は単一のタスクが直列に行い、各所からはキューへ投入するだけにする。
        # 送信側で詰まっても、投入する側(特に優先度1の文字起こし)を待たせないため。
        self._send_queue: asyncio.Queue[object] = asyncio.Queue()
        self._sender_task: asyncio.Task | None = None
        self._send_failed = False

        # 直近の確定セグメント。回答提案のコンテキスト組み立てに使う。
        history_size = (
            settings.answer_suggestion_question_segments
            + settings.answer_suggestion_context_segments
        )
        self._final_segments: deque[TranscriptEvent] = deque(maxlen=history_size)
        # 確定セグメントの累積数。grace period 中の待機側が「自分が待ち始めてから
        # 新しいセグメントが増えたか」を判定するために使う(Eventでは待機開始前の
        # 通知を取りこぼすため、単調増加のカウンタで比較する)。
        self._final_segment_count = 0
        self._segment_arrived = asyncio.Condition()

        # LLMプロバイダは初回リクエスト時に生成する。APIキー未設定などの初期化失敗で
        # 文字起こし(優先度1)まで止めないため、セッション開始時には生成しない。
        self._llm_provider: LlmProvider | None = None
        self._answer_tasks: set[asyncio.Task] = set()
        self._answer_semaphore = asyncio.Semaphore(settings.answer_suggestion_max_concurrency)

    async def start(self) -> None:
        self._sender_task = asyncio.create_task(self._run_sender_loop())
        self._send(StatusEvent(stage="stt_connected", message="STT provider ready"))
        self._stt_task = asyncio.create_task(self._run_stt_loop())

    async def push_audio_chunk(self, chunk: bytes) -> None:
        await self._audio_queue.put(chunk)

    async def stop(self, *, notify_client: bool = True) -> None:
        """セッションを終了する。

        notify_client=False は WebSocket が既に切れている場合に使う。生成中の
        回答提案を待たずに畳み、クライアントへの送信も行わない。
        """
        await self._audio_queue.put(_QUEUE_DONE)
        if self._stt_task is not None:
            await self._stt_task

        if notify_client:
            # 生成中の回答提案は結果を届けてから閉じる(捨てるとカードが生成中のまま
            # 残る)。ただし待ちは有限にする。クライアントは session_stopped を数秒で
            # 待つのをやめるため、それを超えて待っても結果は届かない。
            await self._await_answer_tasks(settings.answer_suggestion_stop_grace_ms / 1000)
            self._send(StatusEvent(stage="session_stopped", message="Session stopped"))
        else:
            await self._cancel_answer_tasks()

        await self._drain_sender(flush=notify_client)

    async def _await_answer_tasks(self, timeout_sec: float) -> None:
        """生成中の回答提案を待つ。期限を超えた分はキャンセルし、error を届ける。"""
        if not self._answer_tasks:
            return

        pending = set(self._answer_tasks)
        _, still_pending = await asyncio.wait(pending, timeout=timeout_sec)
        if still_pending:
            logger.info("Cancelling %d answer suggestion task(s) on stop", len(still_pending))
            await self._cancel_answer_tasks()

    async def _cancel_answer_tasks(self) -> None:
        if not self._answer_tasks:
            return
        tasks = list(self._answer_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _audio_chunk_iterator(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._audio_queue.get()
            if item is _QUEUE_DONE:
                return
            yield item  # type: ignore[misc]

    async def _run_stt_loop(self) -> None:
        try:
            async for event in self._stt_provider.stream_transcribe(self._audio_chunk_iterator()):
                event.audio_source = self._audio_source
                await self.on_final_segment(event)
        except Exception:
            logger.exception("STT loop failed")
            self._send(StatusEvent(stage="error", message="STT processing failed"))

    async def on_final_segment(self, segment: TranscriptEvent) -> None:
        # 履歴の更新と通知はネットワーク送信より先に行う。送信が滞っても、待っている
        # 回答提案が質問末尾を取りこぼさないようにするため。
        if segment.is_final:
            self._final_segments.append(segment)
            self._final_segment_count += 1
            async with self._segment_arrived:
                self._segment_arrived.notify_all()

        self._send(segment)  # 優先度1: 即座にキューへ投入(送信完了は待たない)

        # 優先度2・4・5(ファクトチェック・想定質問生成・話者判別)は後続Phaseで
        # asyncio.create_task(...) として非同期・fire-and-forgetで追加していく。
        # 優先度3(回答提案)はセグメント確定ではなくUI操作を起点に発火するため、
        # この経路には含まれない(request_answer_suggestion を参照)。

    def request_answer_suggestion(self, request_id: str) -> None:
        """「回答提案」ボタン押下を受けて生成タスクを起動する(fire-and-forget)。

        文字起こしパイプラインをブロックしないよう、待機・生成はすべてタスク側で行う。
        """
        # grace period の基準は「押下を受け取った時点」の確定数。タスク開始まで待つと、
        # その間に確定したセグメントを到着済みと誤認し、猶予を満額待ってしまう。
        seen_count = self._final_segment_count
        task = asyncio.create_task(self._generate_answer_suggestion(request_id, seen_count))
        self._answer_tasks.add(task)
        task.add_done_callback(self._answer_tasks.discard)

    async def _generate_answer_suggestion(self, request_id: str, seen_count: int) -> None:
        try:
            self._send(AnswerSuggestionEvent(request_id=request_id, status="generating"))
            await self._await_grace_period(seen_count)
            context = self._build_answer_context()

            if not context.question_turns:
                self._send(
                    AnswerSuggestionEvent(
                        request_id=request_id,
                        status="error",
                        message="まだ文字起こしがありません。発話後にもう一度お試しください。",
                    )
                )
                return

            if self._llm_provider is None:
                self._llm_provider = get_llm_provider(settings.llm_provider)

            async with self._answer_semaphore:
                # LLMが返らない場合も generating のまま放置せず error へ落とすため、
                # 呼び出し側で打ち切る(プロバイダ実装のtimeout有無に依存しない)。
                answer = await asyncio.wait_for(
                    self._llm_provider.generate_answer_suggestion(context),
                    timeout=settings.answer_suggestion_timeout_ms / 1000,
                )

            self._send(
                AnswerSuggestionEvent(
                    request_id=request_id,
                    status="done",
                    answer=answer,
                    source_segment_ids=context.source_segment_ids,
                )
            )
        except asyncio.CancelledError:
            # セッション終了・切断による打ち切り。クライアントは既に離れているため
            # 通知はしない(送信キューも畳まれる)。
            raise
        except asyncio.TimeoutError:
            logger.warning("Answer suggestion timed out: request_id=%s", request_id)
            self._send(
                AnswerSuggestionEvent(
                    request_id=request_id,
                    status="error",
                    message="回答提案の生成が時間内に終わりませんでした。",
                )
            )
        except Exception:
            # 回答提案の失敗は文字起こしに影響させない(独立して失敗してよい設計)。
            logger.exception("Answer suggestion failed: request_id=%s", request_id)
            self._send(
                AnswerSuggestionEvent(
                    request_id=request_id,
                    status="error",
                    message="回答提案の生成に失敗しました。",
                )
            )

    async def _await_grace_period(self, seen_count: int) -> None:
        """押下時点では質問末尾がまだ確定していない可能性が高いため、確定セグメントの
        到着を猶予時間だけ待つ。

        `seen_count` は押下を受け取った時点の確定数。これを超えるセグメントが確定
        すれば即座に打ち切って生成へ進む。待つのは1回だけで、発話が続いていても
        待ち直さない。押下は通常「質問を言い終わった後」に行われるため待ち直す利得は
        小さく、待ち直すと2〜3秒のレイテンシ目標を超えてしまうため(発話が途切れない
        限り生成が始まらない)。

        新しいセグメントが来なくても猶予時間は待つ。押下直後に発話が続くかどうかは
        判定できず、待たずに生成すると質問末尾を取りこぼすため。体感を速くしたい
        場合は ANSWER_SUGGESTION_GRACE_MS を下げる。
        """
        grace_sec = settings.answer_suggestion_grace_ms / 1000
        if grace_sec <= 0:
            return

        try:
            async with self._segment_arrived:
                await asyncio.wait_for(
                    self._segment_arrived.wait_for(
                        lambda: self._final_segment_count > seen_count
                    ),
                    timeout=grace_sec,
                )
        except asyncio.TimeoutError:
            # 猶予時間内に新しい確定セグメントが来なかった = 発話が途切れている。
            pass

    def _build_answer_context(self) -> AnswerSuggestionContext:
        """直近セグメントを質問候補、それ以前を文脈として分ける(ARCHITECTURE 4.4)。

        「どこからどこまでが質問か」の境界は厳密に決めず、プロンプト側で吸収する。
        """
        segments = list(self._final_segments)
        question_count = settings.answer_suggestion_question_segments
        question_segments = segments[-question_count:]
        context_segments = segments[: len(segments) - len(question_segments)]

        return AnswerSuggestionContext(
            question_turns=[_to_turn(s) for s in question_segments],
            context_turns=[_to_turn(s) for s in context_segments],
        )

    def _send(self, message) -> None:
        """送信キューへ投入する。送信完了は待たないため呼び出し側はブロックしない。"""
        if self._send_failed:
            return
        self._send_queue.put_nowait(message)

    async def _run_sender_loop(self) -> None:
        """キューの内容を順に送る単一の送信タスク。

        送信を1箇所に集約することで、各所で送信ロックを取り合わずに済み、
        回答提案の送信が詰まっても文字起こしの投入を妨げない。
        """
        while True:
            message = await self._send_queue.get()
            try:
                if message is _QUEUE_DONE:
                    return
                if self._send_failed:
                    continue
                try:
                    await self._websocket.send_json(message.model_dump())
                except Exception:
                    # 切断後の送信失敗でループを止めない。以降の送信は捨てる。
                    self._send_failed = True
                    logger.info("WebSocket send failed; dropping subsequent events")
            finally:
                self._send_queue.task_done()

    async def _drain_sender(self, *, flush: bool) -> None:
        """送信タスクを終了させる。flush=True ならキューの残りを送り切ってから閉じる。"""
        if self._sender_task is None:
            return
        if not flush:
            self._send_failed = True
        self._send_queue.put_nowait(_QUEUE_DONE)
        await self._sender_task
        self._sender_task = None


def _to_turn(segment: TranscriptEvent) -> ConversationTurn:
    return ConversationTurn(
        segment_id=segment.segment_id,
        speaker_id=segment.speaker_id,
        text=segment.text,
    )
