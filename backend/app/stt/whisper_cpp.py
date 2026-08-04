import asyncio
import json
import logging
import tempfile
import time
import uuid
import wave
from pathlib import Path
from typing import AsyncIterator

from app.config import settings
from app.schemas import TranscriptEvent
from app.stt.vad import SilenceSegmenter

logger = logging.getLogger(__name__)

_MODEL_FILENAME_MAP = {
    "tiny": "ggml-tiny.bin",
    "base": "ggml-base.bin",
    "small": "ggml-small.bin",
    "medium": "ggml-medium.bin",
    "large": "ggml-large-v1.bin",
    "largev2": "ggml-large-v2.bin",
}


class WhisperCppConfigError(RuntimeError):
    pass


def _resolve_binary() -> Path:
    binary_path = settings.whisper_cpp_binary.strip()
    if not binary_path:
        raise WhisperCppConfigError(
            "whisper.cpp バイナリが未設定です。環境変数 WHISPER_CPP_BINARY を設定してください。"
        )
    binary = Path(binary_path).expanduser()
    if not binary.is_file():
        raise WhisperCppConfigError(f"whisper.cpp バイナリが見つかりません: {binary}")
    return binary


def _resolve_model_path() -> Path:
    ggml_filename = _MODEL_FILENAME_MAP.get(settings.whisper_cpp_model)
    if not ggml_filename:
        available = ", ".join(_MODEL_FILENAME_MAP)
        raise WhisperCppConfigError(
            f"未対応の whisper.cpp モデルです: {settings.whisper_cpp_model} (利用可能: {available})"
        )
    model_dir = Path(settings.whisper_cpp_model_dir).expanduser()
    model_path = model_dir / ggml_filename
    if not model_path.is_file():
        raise WhisperCppConfigError(
            f"GGML モデルが見つかりません: {model_path}\n"
            f"scripts/download_whisper_cpp_model.py などで取得してください。"
        )
    return model_path


def _write_wav(pcm_bytes: bytes, wav_path: Path) -> None:
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(settings.audio_channels)
        wav_file.setsampwidth(2)  # PCM16
        wav_file.setframerate(settings.audio_sample_rate)
        wav_file.writeframes(pcm_bytes)


def _parse_whisper_cpp_json(json_path: Path) -> str:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    transcription = data.get("transcription") or []
    texts = []
    for item in transcription:
        if not isinstance(item, dict):
            continue
        text = (item.get("text") or "").strip()
        if text:
            texts.append(text)
    return "".join(texts).strip()


async def _run_whisper_cli(binary: Path, model_path: Path, wav_path: Path) -> str:
    with tempfile.NamedTemporaryFile(suffix="", delete=False) as out_tmp:
        out_prefix = Path(out_tmp.name)
    out_prefix.unlink(missing_ok=True)
    json_path = Path(str(out_prefix) + ".json")

    cmd = [
        str(binary),
        "-m",
        str(model_path),
        "-f",
        str(wav_path),
        "-l",
        settings.stt_language,
        "-bs",
        str(settings.whisper_cpp_beam_size),
        "--device",
        str(settings.whisper_cpp_gpu_device),
        "-oj",
        "-of",
        str(out_prefix),
    ]
    if not settings.whisper_cpp_flash_attn:
        cmd.append("--no-flash-attn")
    if settings.whisper_cpp_threads > 0:
        cmd.extend(["-t", str(settings.whisper_cpp_threads)])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(
                f"whisper-cli が終了コード {proc.returncode} で失敗しました: {stderr.decode(errors='ignore')}"
            )
        if not json_path.is_file():
            raise RuntimeError(f"whisper-cli の JSON 出力がありません: {json_path}")

        return _parse_whisper_cpp_json(json_path)
    finally:
        json_path.unlink(missing_ok=True)


class WhisperCppSttProvider:
    """whisper.cpp(Vulkanビルド等)の whisper-cli をVAD区切りごとにsubprocess実行するローカルSTT実装。"""

    def __init__(self) -> None:
        self._binary = _resolve_binary()
        self._model_path = _resolve_model_path()
        self._segmenter = SilenceSegmenter(
            sample_rate=settings.audio_sample_rate,
            energy_threshold=settings.vad_energy_threshold,
            silence_threshold_ms=settings.vad_silence_threshold_ms,
        )

    async def stream_transcribe(
        self, audio_chunks: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscriptEvent]:
        async for chunk in audio_chunks:
            segment_pcm = self._segmenter.push(chunk)
            if segment_pcm is not None:
                event = await self._transcribe_segment(segment_pcm)
                if event is not None:
                    yield event

        remaining = self._segmenter.flush_remaining()
        if remaining is not None:
            event = await self._transcribe_segment(remaining)
            if event is not None:
                yield event

    async def _transcribe_segment(self, pcm_bytes: bytes) -> TranscriptEvent | None:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_tmp:
            wav_path = Path(wav_tmp.name)
        try:
            _write_wav(pcm_bytes, wav_path)
            start_ts = time.monotonic()
            text = await _run_whisper_cli(self._binary, self._model_path, wav_path)
            end_ts = time.monotonic()
        except Exception:
            logger.exception("whisper.cpp transcription failed")
            return None
        finally:
            wav_path.unlink(missing_ok=True)

        if not text:
            return None

        return TranscriptEvent(
            segment_id=str(uuid.uuid4()),
            audio_source="microphone",
            text=text,
            is_final=True,
            start_ts=start_ts,
            end_ts=end_ts,
        )
