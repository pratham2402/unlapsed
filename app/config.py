from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Unlapsed"
    app_base_url: str = "http://127.0.0.1:8000"
    app_secret_key: str = "change-me"
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'unlapsed.db'}"
    demo_user_email: str = "alex@unlapsed.demo"
    demo_read_only: bool = False

    nebius_api_key: str = ""
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    nebius_model_fast: str = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    nebius_model_reasoning: str = "nvidia/nemotron-3-super-120b-a12b"

    tavily_api_key: str = ""

    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "unlapsed"

    toloka_api_key: str = ""
    tandem_api_key: str = ""

    data_dir: Path = BASE_DIR / "data"
    samples_dir: Path = BASE_DIR / "data" / "samples"
    generated_dir: Path = BASE_DIR / "data" / "generated"

    @property
    def live_nebius(self) -> bool:
        return bool(self.nebius_api_key.strip())

    @property
    def live_tavily(self) -> bool:
        return bool(self.tavily_api_key.strip())

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.samples_dir.mkdir(parents=True, exist_ok=True)
        self.generated_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
