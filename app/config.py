from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    supabase_url: str
    supabase_jwt_audience: str = "authenticated"
    cors_origins: str = "http://localhost:5173"
    log_level: str = "info"

    # Pool sized for four users on a single uvicorn process. Supabase's session
    # pooler is the other half of this number — raising it here alone does nothing.
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_echo: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def supabase_auth_url(self) -> str:
        """A trailing slash in SUPABASE_URL would produce `...supabase.co//auth/v1`.

        That silently fails every token on the issuer check rather than on anything
        that names the cause, so it is stripped once here instead of at both call sites.
        """
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    @property
    def supabase_jwks_url(self) -> str:
        return f"{self.supabase_auth_url}/.well-known/jwks.json"

    @property
    def supabase_issuer(self) -> str:
        """Expected `iss` claim. Supabase signs with the project's own auth URL."""
        return self.supabase_auth_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
