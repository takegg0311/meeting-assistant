import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", env_file_encoding="utf-8", extra="ignore")

    backend_port: int = 8000
    frontend_port: int = 3000
    env: str = "development"
    # 未設定なら FRONTEND_PORT から組み立てる(cors_origins プロパティを参照)。
    # 別ホストからアクセスさせる場合のみ明示する。
    cors_origin: str = ""
    stt_provider: str = "mock"
    audio_sample_rate: int = 16000
    audio_channels: int = 1
    log_level: str = "info"

    # --- OpenAI Realtime STT (cloud_openai) ---
    openai_api_key: str = ""
    openai_realtime_model: str = "gpt-4o-transcribe"
    openai_realtime_url: str = "wss://api.openai.com/v1/realtime"
    stt_language: str = "ja"

    # --- Google Cloud Speech-to-Text StreamingRecognize (cloud_google) ---
    google_application_credentials: str = ""
    google_stt_language_code: str = "ja-JP"

    # --- whisper.cpp Vulkanビルド (local_whispercpp) ---
    whisper_cpp_binary: str = ""
    whisper_cpp_model_dir: str = "./models/whisper_cpp"
    whisper_cpp_model: str = "base"
    whisper_cpp_beam_size: int = 5
    whisper_cpp_flash_attn: bool = False
    whisper_cpp_gpu_device: int = 0
    whisper_cpp_threads: int = 0

    # --- VAD (whisper.cpp区切り検出用) ---
    vad_silence_threshold_ms: int = 700
    vad_energy_threshold: float = 0.01

    @property
    def cors_origins(self) -> list[str]:
        """CORS許可オリジン。CORS_ORIGIN未設定時は FRONTEND_PORT から組み立てる。"""
        if self.cors_origin:
            return [self.cors_origin]
        return [
            f"http://localhost:{self.frontend_port}",
            f"http://127.0.0.1:{self.frontend_port}",
        ]


settings = Settings()

if settings.google_application_credentials and "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = settings.google_application_credentials
