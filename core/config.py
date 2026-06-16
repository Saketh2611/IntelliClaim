from pydantic_settings import BaseSettings
from pydantic import Field
from dotenv import load_dotenv

load_dotenv()


class Settings(BaseSettings):
    supabase_url: str = Field(..., env="SUPABASE_URL")
    supabase_service_role_key: str | None = Field(None, env="SUPABASE_SERVICE_ROLE_KEY")
    # support legacy env name SUPABASE_KEY
    supabase_key: str | None = Field(None, env="SUPABASE_KEY")
    anthropic_api_key: str | None = Field(None, env="ANTHROPIC_API_KEY")
    gemini_api_key: str | None = Field(None, env="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-3.5-flash")
    api_key: str = Field("dev-key", env="AI_API_KEY")
    upload_dir: str = Field("uploads")
    policy_path: str = Field("policy_terms.json")
    environment: str = Field("development")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

# If a legacy SUPABASE_KEY is present, use it for supabase_service_role_key
if not settings.supabase_service_role_key and settings.supabase_key:
    settings.supabase_service_role_key = settings.supabase_key