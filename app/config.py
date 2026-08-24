from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


# Keep this list deliberately explicit: the web UI only offers models that the
# service has been tested/configured to call via the Responses API.
AVAILABLE_MODELS = [
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.4-mini",
    "gpt-5.2",
    "gpt-5.1",
    "gpt-5",
    "gpt-5-mini",
    "gpt-4o",
]


class Settings(BaseSettings):
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4-mini"
    max_upload_mb: int = 30
    max_pages: int = 500
    batch_max_chars: int = 14_000
    # Reviewer v3.2 har dessutom ett hårt säkerhetsgolv på 5, så en äldre .env
    # med ett lägre värde kan inte återaktivera för aggressiv early stopping.
    multipass_min_runs: int = 5
    multipass_max_runs: int = 10
    multipass_saturation_ratio: float = 0.10
    multipass_saturation_streak: int = 2
    keep_run_files: bool = True
    uploads_dir: Path = Path("uploads")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()