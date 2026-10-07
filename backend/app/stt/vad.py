import math
import struct


def pcm_level_dbfs(chunk: bytes) -> float:
    """PCM16LEのRMS音量。0 dBFSが最大音量。不完全な末尾サンプルは無視。"""
    samples = [sample[0] for sample in struct.iter_unpack("<h", chunk[:len(chunk) // 2 * 2])]
    if not samples:
        return -math.inf
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768
    return 20 * math.log10(rms) if rms else -math.inf


class SilenceDetector:
    """全STT共通の音量VAD。時間は受信速度ではなくPCMの音声長から算出する。"""

    def __init__(self, sample_rate: int, threshold_dbfs: float = -45.0,
                 silence_seconds: float = 0.8, min_seconds: float = 0.0):
        self.sample_rate = sample_rate
        self.threshold_dbfs = threshold_dbfs
        self.silence_seconds = silence_seconds
        self.min_seconds = min_seconds
        self.reset()

    def reset(self) -> None:
        self.audio_seconds = 0.0
        self.quiet_seconds = 0.0
        self.has_speech = False

    def push(self, chunk: bytes) -> bool:
        duration = (len(chunk) // 2) / self.sample_rate
        self.audio_seconds += duration
        if pcm_level_dbfs(chunk) > self.threshold_dbfs:
            self.has_speech = True
            self.quiet_seconds = 0.0
        else:
            self.quiet_seconds += duration
        return (self.has_speech and self.audio_seconds >= self.min_seconds
                and self.quiet_seconds + 1e-9 >= self.silence_seconds)


class SilenceSegmenter:
    """共通VADで発話をまとめる。長い発話はバッファ肥大化を避けるため区切る。"""

    def __init__(self, sample_rate: int, threshold_dbfs: float = -45.0,
                 max_seconds: float = 30.0):
        self.detector = SilenceDetector(sample_rate, threshold_dbfs)
        self.max_seconds = max_seconds
        self._buffer = bytearray()

    def push(self, chunk: bytes) -> bytes | None:
        boundary = self.detector.push(chunk)
        if not self.detector.has_speech:
            self.detector.reset()
            return None
        self._buffer.extend(chunk)
        if boundary or self.detector.audio_seconds >= self.max_seconds:
            return self.flush_remaining()
        return None

    def flush_remaining(self) -> bytes | None:
        segment = bytes(self._buffer) if self.detector.has_speech and self._buffer else None
        self._buffer.clear()
        self.detector.reset()
        return segment
