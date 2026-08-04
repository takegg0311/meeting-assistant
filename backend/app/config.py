from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", env_file_encoding="utf-8", extra="ignore")

    backend_port: int = 8000
    env: str = "development"
    cors_origin: str = "http://localhost:3000"
    stt_provider: str = "mock"
    audio_sample_rate: int = 16000
    audio_channels: int = 1
    log_level: str = "info"


settings = Settings()
