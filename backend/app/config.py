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
    llm_provider: str = "mock"
    audio_sample_rate: int = 16000
    audio_channels: int = 1
    log_level: str = "info"

    # --- LLM (回答提案などの生成処理) ---
    anthropic_api_key: str = ""
    # 2〜3秒目標のレイテンシ要求のため、回答提案は軽量モデルを既定にする。
    anthropic_answer_model: str = "claude-haiku-4-5-20251001"
    anthropic_answer_max_tokens: int = 1024

    # --- 回答提案 (Phase 2) ---
    # ボタン押下時点では質問末尾がSTT未確定な可能性が高いため、押下後に確定
    # セグメントを待つ猶予時間。待機中はUIに生成中カードを表示する。
    answer_suggestion_grace_ms: int = 1500
    # 質問候補として扱う直近セグメント数と、その前に文脈として渡すセグメント数。
    answer_suggestion_question_segments: int = 2
    answer_suggestion_context_segments: int = 8
    # 同時に走らせる生成タスクの上限(連打時の保護)。
    answer_suggestion_max_concurrency: int = 3
    # LLM生成の打ち切り時間。超えるとカードをerror表示に落とす(generatingのまま
    # 放置しない)。2〜3秒目標に対し、遅延時のリトライ余地を含めた上限。
    answer_suggestion_timeout_ms: int = 15000
    # セッション終了時に生成中の回答提案を待つ上限。クライアントは session_stopped を
    # 数秒で待つのをやめるため、それを超えて待っても結果は届かない。
    answer_suggestion_stop_grace_ms: int = 3000

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
