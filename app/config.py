from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://spb:spb@localhost:5432/spb"
    session_secret: str = "cambiar-en-produccion"
    adk_webhook_url: str | None = None

    # Agentes (ver app/agents). provider: none | stub | adk
    agent_provider: str = "none"
    agent_model: str = "gemini-3.1-pro-preview"
    agent_execution: str = "background"  # background | inline
    agent_auto_refine: bool = False
    agent_max_doc_chars: int = 200_000
    agent_docs_dir: str = "documents"
    google_api_key: str | None = None
    agent_price_in_per_1m: float | None = None
    agent_price_out_per_1m: float | None = None


    @property
    def agent_docs_dir_path(self) -> Path:
        return Path(self.agent_docs_dir).expanduser().resolve()


settings = Settings()
