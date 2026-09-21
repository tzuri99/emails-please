"""Settings. Everything overridable by environment, nothing secret in code."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+pysqlite:///./sdoc.db"
    data_source: str = "../data"
    gemini_api_key: str = ""
    firebase_project_id: str = ""
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def origins(self):
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
