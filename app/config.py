from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openai_api_key: str | None = None
    openai_model: str = "gpt-5-mini"
    max_upload_mb: int = 30
    max_pages: int = 500
    batch_max_chars: int = 14_000
    keep_run_files: bool = True
    runs_dir: Path = Path("runs")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
