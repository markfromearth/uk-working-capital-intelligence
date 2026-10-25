import os
from typing import List, Optional
from pydantic_settings import BaseSettings

INSECURE_DEFAULT_SECRETS = {
    "",
    "prod-secret-change-in-production-uk-working-capital-2026",
    "change_me_in_production_generate_with_openssl_rand_hex_32",
    "change-me",
    "secret",
    "dev-only-insecure-secret-key-not-for-production-2026",
}


class Settings(BaseSettings):
    PROJECT_NAME: str = "UK Working-Capital Intelligence Platform"
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = os.getenv(
        "SECRET_KEY",
        "dev-only-insecure-secret-key-not-for-production-2026",
    )
    AUDIT_CHECKPOINT_SECRET: Optional[str] = os.getenv("AUDIT_CHECKPOINT_SECRET", None)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./working_capital.db")

    # Environment: development | test | staging | production
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Explicit First-Admin Bootstrap (Never hardcoded!)
    BOOTSTRAP_ADMIN_EMAIL: Optional[str] = os.getenv("BOOTSTRAP_ADMIN_EMAIL", None)
    BOOTSTRAP_ADMIN_PASSWORD: Optional[str] = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", None)
    BOOTSTRAP_ADMIN_ROLE: str = os.getenv("BOOTSTRAP_ADMIN_ROLE", "admin")

    # Provider API Credentials (Optional / Live vs Synthetic)
    COMPANIES_HOUSE_API_KEY: Optional[str] = os.getenv("COMPANIES_HOUSE_API_KEY", None)
    CREDITSAFE_API_KEY: Optional[str] = os.getenv("CREDITSAFE_API_KEY", None)
    CREDITSAFE_WEBHOOK_SECRET: Optional[str] = os.getenv("CREDITSAFE_WEBHOOK_SECRET", None)
    EXPERIAN_API_KEY: Optional[str] = os.getenv("EXPERIAN_API_KEY", None)
    PROXYCURL_API_KEY: Optional[str] = os.getenv("PROXYCURL_API_KEY", None)
    ANTHROPIC_API_KEY: Optional[str] = os.getenv("ANTHROPIC_API_KEY", None)

    # Operational switches
    DEMO_MODE: bool = False
    ENABLE_DEAD_LETTER_QUEUE: bool = True
    MAX_RETRY_ATTEMPTS: int = 3
    RATE_LIMIT_PER_MINUTE: int = 120
    MAX_REQUEST_BODY_BYTES: int = 1024 * 1024  # 1 MB

    # CORS (Explicit origins only; never wildcard with credentials)
    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

    model_config = {"env_file": ".env", "case_sensitive": True, "extra": "allow"}

    def validate_production_security(self) -> None:
        """
        Fail-closed security posture validation for production and staging environments.
        Raises RuntimeError if insecure defaults, weak signing secrets, demo mode, or
        wildcard CORS are configured.
        """
        env = (self.ENVIRONMENT or "").strip().lower()
        if env in ("production", "staging"):
            secret = (self.SECRET_KEY or "").strip()
            if secret in INSECURE_DEFAULT_SECRETS or len(secret) < 32:
                raise RuntimeError(
                    "Fail-closed startup error: SECRET_KEY is missing, weak (<32 chars), "
                    "or set to a known default placeholder in production/staging."
                )
            if self.DEMO_MODE:
                raise RuntimeError(
                    "Fail-closed startup error: DEMO_MODE=True is strictly forbidden in production/staging."
                )
            if "*" in self.BACKEND_CORS_ORIGINS:
                raise RuntimeError(
                    "Fail-closed startup error: Wildcard '*' in BACKEND_CORS_ORIGINS is forbidden in production/staging."
                )
            if "capital_password" in (self.DATABASE_URL or ""):
                raise RuntimeError(
                    "Fail-closed startup error: Default example database password detected in DATABASE_URL."
                )
            if self.BOOTSTRAP_ADMIN_PASSWORD and len(self.BOOTSTRAP_ADMIN_PASSWORD) < 12:
                raise RuntimeError(
                    "Fail-closed startup error: BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters."
                )


settings = Settings()
