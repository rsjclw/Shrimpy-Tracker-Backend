from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    supabase_url: str
    supabase_service_role_key: str = ""
    frontend_origin: str = "http://localhost:3000"
    admin_emails: str = ""
    # Used for lunar windows when a grid has no coordinates yet, and as the
    # fallback schedule zone for weather syncing.
    default_timezone: str = "Asia/Jakarta"
    weather_enabled: bool = True
    weather_sync_hours: str = "5,17"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
