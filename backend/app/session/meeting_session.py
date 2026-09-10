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
        # 複数の回答提案を並行して走らせるため、送信の直列化のみ担保する。
        self._send_lock = asyncio.Lock()

    async def start(self) -> None:
        await self._send(StatusEvent(stage="stt_connected", message="STT provider ready"))
        self._stt_task = asyncio.create_task(self._run_stt_loop())

    async def push_audio_chunk(self, chunk: bytes) -> None:
        await self._audio_queue.put(chunk)

    async def stop(self) -> None:
        await self._audio_queue.put(_QUEUE_DONE)
        if self._stt_task is not None:
            await self._stt_task

        # 生成中の回答提案は結果を届けてから閉じる(捨てるとカードが生成中のまま残る)。
        if self._answer_tasks:
            await asyncio.gather(*self._answer_tasks, return_exceptions=True)

        await self._send(StatusEvent(stage="session_stopped", message="Session stopped"))

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
            await self._send(StatusEvent(stage="error", message="STT processing failed"))

    async def on_final_segment(self, segment: TranscriptEvent) -> None:
        await self._send(segment)  # 優先度1: 即座に配信

        if segment.is_final:
            self._final_segments.append(segment)
            self._final_segment_count += 1
            # grace period 中の待機を起こす。
            async with self._segment_arrived:
                self._segment_arrived.notify_all()

        # 優先度2・4・5(ファクトチェック・想定質問生成・話者判別)は後続Phaseで
        # asyncio.create_task(...) として非同期・fire-and-forgetで追加していく。
        # 優先度3(回答提案)はセグメント確定ではなくUI操作を起点に発火するため、
        # この経路には含まれない(request_answer_suggestion を参照)。

    def request_answer_suggestion(self, request_id: str) -> None:
        """「回答提案」ボタン押下を受けて生成タスクを起動する(fire-and-forget)。

        文字起こしパイプラインをブロックしないよう、待機・生成はすべてタスク側で行う。
        """
        task = asyncio.create_task(self._generate_answer_suggestion(request_id))
        self._answer_tasks.add(task)
        task.add_done_callback(self._answer_tasks.discard)

    async def _generate_answer_suggestion(self, request_id: str) -> None:
        try:
            await self._send(AnswerSuggestionEvent(request_id=request_id, status="generating"))
            await self._await_grace_period()
            context = self._build_answer_context()

            if not context.question_turns:
                await self._send(
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
                answer = await self._llm_provider.generate_answer_suggestion(context)

            await self._send(
                AnswerSuggestionEvent(
                    request_id=request_id,
                    status="done",
                    answer=answer,
                    source_segment_ids=context.source_segment_ids,
                )
            )
        except Exception:
            # 回答提案の失敗は文字起こしに影響させない(独立して失敗してよい設計)。
            logger.exception("Answer suggestion failed: request_id=%s", request_id)
            await self._send(
                AnswerSuggestionEvent(
                    request_id=request_id,
                    status="error",
                    message="回答提案の生成に失敗しました。",
                )
            )

    async def _await_grace_period(self) -> None:
        """押下時点では質問末尾がまだ確定していない可能性が高いため、確定セグメントの
        到着を猶予時間だけ待つ。

        猶予時間内に新しいセグメントが確定すれば即座に打ち切って生成へ進む。
        待つのは1回だけで、発話が続いていても待ち直さない。押下は通常「質問を
        言い終わった後」に行われるため待ち直す利得は小さく、待ち直すと2〜3秒の
        レイテンシ目標を超えてしまうため(発話が途切れない限り生成が始まらない)。

        新しいセグメントが来なくても猶予時間は待つ。押下直後に発話が続くかどうかは
        判定できず、待たずに生成すると質問末尾を取りこぼすため。体感を速くしたい
        場合は ANSWER_SUGGESTION_GRACE_MS を下げる。
        """
        grace_sec = settings.answer_suggestion_grace_ms / 1000
        if grace_sec <= 0:
            return

        seen_count = self._final_segment_count
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

    async def _send(self, message) -> None:
        # 回答提案が並行して完了しても送信が交錯しないよう直列化する。
        async with self._send_lock:
            await self._websocket.send_json(message.model_dump())


def _to_turn(segment: TranscriptEvent) -> ConversationTurn:
    return ConversationTurn(
        segment_id=segment.segment_id,
        speaker_id=segment.speaker_id,
        text=segment.text,
    )
