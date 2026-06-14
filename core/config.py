from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    supabase_url:         str
    supabase_service_role_key: str
    anthropic_api_key:    str
    api_key:              str = "dev-key"
    upload_dir:           str = "uploads"
    policy_path:          str = "policy_terms.json"
    environment:          str = "development"

    class Config:
        env_file = ".env"

settings = Settings()