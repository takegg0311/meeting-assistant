import math
import time
from array import array


class SilenceSegmenter:
    """PCM16LEチャンクのRMSエネルギーを見て、無音がsilence_threshold_ms続いたら
    それまでに溜めたチャンクを1発話セグメントとして確定させる。

    whisper.cpp(subprocessバッチ実行)向けに、確定した発話区間の音声をまとめて渡すために使う。
    クラウド系(OpenAI Realtime / Google StreamingRecognize)はサーバー側VADを内蔵するため使用しない。
    """

    def __init__(
        self,
        sample_rate: int,
        energy_threshold: float = 0.01,
        silence_threshold_ms: int = 700,
    ):
        self._sample_rate = sample_rate
        self._energy_threshold = energy_threshold
        self._silence_threshold_ms = silence_threshold_ms

        self._buffer = bytearray()
        self._silence_started_at: float | None = None
        self._has_speech = False

    def push(self, chunk: bytes) -> bytes | None:
        """chunkを取り込む。無音区間確定によりセグメントが完了したら、そのPCMバイト列を返す。"""
        is_speech = self._is_speech(chunk)
        self._buffer.extend(chunk)

        if is_speech:
            self._has_speech = True
            self._silence_started_at = None
            return None

        if not self._has_speech:
            # まだ発話が始まっていない無音は捨てる(バッファ肥大化防止)
            self._buffer.clear()
            return None

        now = time.monotonic()
        if self._silence_started_at is None:
            self._silence_started_at = now
            return None

        elapsed_ms = (now - self._silence_started_at) * 1000
        if elapsed_ms >= self._silence_threshold_ms:
            return self._flush()
        return None

    def flush_remaining(self) -> bytes | None:
        """セッション終了時などに、無音確定を待たず残りのバッファを取り出す。"""
        if self._has_speech and len(self._buffer) > 0:
            return self._flush()
        return None

    def _flush(self) -> bytes:
        segment = bytes(self._buffer)
        self._buffer.clear()
        self._has_speech = False
        self._silence_started_at = None
        return segment

    def _is_speech(self, chunk: bytes) -> bool:
        if len(chunk) < 2:
            return False
        # 奇数バイトは不完全なサンプルとして無視する
        usable_len = len(chunk) - (len(chunk) % 2)
        samples = array("h")
        samples.frombytes(chunk[:usable_len])
        if len(samples) == 0:
            return False

        sum_squares = sum(sample * sample for sample in samples)
        rms = math.sqrt(sum_squares / len(samples))
        normalized = rms / 32768.0
        return normalized >= self._energy_threshold
