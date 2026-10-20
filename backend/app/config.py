import os
from typing import List, Optional
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "UK Working-Capital Intelligence Platform"
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "prod-secret-change-in-production-uk-working-capital-2026")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./working_capital.db")
    
    # Environment
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    # Provider API Credentials (Optional / Live vs Synthetic)
    COMPANIES_HOUSE_API_KEY: Optional[str] = os.getenv("COMPANIES_HOUSE_API_KEY", None)
    CREDITSAFE_API_KEY: Optional[str] = os.getenv("CREDITSAFE_API_KEY", None)
    CREDITSAFE_WEBHOOK_SECRET: Optional[str] = os.getenv("CREDITSAFE_WEBHOOK_SECRET", None)
    EXPERIAN_API_KEY: Optional[str] = os.getenv("EXPERIAN_API_KEY", None)
    PROXYCURL_API_KEY: Optional[str] = os.getenv("PROXYCURL_API_KEY", None)
    ANTHROPIC_API_KEY: Optional[str] = os.getenv("ANTHROPIC_API_KEY", None)
    
    # Operational switches
    DEMO_MODE: bool = True
    ENABLE_DEAD_LETTER_QUEUE: bool = True
    MAX_RETRY_ATTEMPTS: int = 3
    RATE_LIMIT_PER_MINUTE: int = 120
    
    # CORS
    BACKEND_CORS_ORIGINS: List[str] = ["*"]

    model_config = {"env_file": ".env", "case_sensitive": True, "extra": "allow"}

settings = Settings()
