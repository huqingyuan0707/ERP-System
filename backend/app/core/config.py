import logging
import os
import re
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


logger = logging.getLogger(__name__)

_INSECURE_PRODUCTION_SECRETS = {
    "change_me",
    "change-me",
    "changeme",
    "dev-secret",
    "dev-secret-key",
    "development-secret",
    "development-secret-key",
    "development-only-secret-key",
    "development-secret-key-change-me",
    "isolated-test-secret-not-for-deployment",
    "secret",
    "secret-key",
    "test-secret",
    "test-secret-key",
    "your-secret-key",
}


class Settings(BaseSettings):
    """全局配置，可通过环境变量或 backend/.env 覆盖。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    PROJECT_NAME: str = "企业ERP系统"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: Literal["development", "test", "production"] = "development"

    # 默认值仅用于开发；production 会在启动前拒绝占位值和不安全配置。
    DATABASE_URL: str = "postgresql+asyncpg://erp:change_me@127.0.0.1:5432/erp"

    SECRET_KEY: str = "change_me"
    INITIAL_ADMIN_PASSWORD: str | None = None
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 10080
    JWT_ALGORITHM: str = "HS256"

    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    REDIS_URL: str | None = None

    LOG_LEVEL: str = "INFO"

    DEBUG: bool | None = None

    # 测试环境开关：为 True 时数据库连接池使用 NullPool
    # （测试每个用例独立事件循环，池化连接跨循环复用会报 'NoneType' object has no attribute 'send'）
    TESTING: bool = False

    @model_validator(mode="after")
    def validate_environment_security(self) -> "Settings":
        """Set safe defaults and reject deployment placeholders in production."""
        if self.DEBUG is None:
            self.DEBUG = self.ENVIRONMENT == "development" and not self.TESTING

        if self.ENVIRONMENT != "production":
            return self

        secret = self.SECRET_KEY.strip()
        if len(secret) < 32 or secret.lower() in _INSECURE_PRODUCTION_SECRETS:
            raise ValueError(
                "生产环境 SECRET_KEY 必须是至少 32 个字符的强随机值，不能使用空值、"
                "change_me 或常见开发密钥"
            )

        database_url = self.DATABASE_URL.strip()
        if (
            not database_url
            or "change_me" in database_url.lower()
            or database_url.lower().startswith("sqlite")
        ):
            raise ValueError(
                "生产环境 DATABASE_URL 必须使用非空的非 SQLite 连接串，且不能包含 change_me"
            )

        admin_password = self.INITIAL_ADMIN_PASSWORD or ""
        if (
            len(admin_password) < 8
            or re.search(r"[A-Za-z]", admin_password) is None
            or re.search(r"\d", admin_password) is None
        ):
            raise ValueError(
                "生产环境 INITIAL_ADMIN_PASSWORD 必须显式配置，且至少 8 位并同时包含字母和数字"
            )

        return self


# 测试只使用显式环境配置，不读取开发机的 .env。
settings = (
    Settings(_env_file=None) if os.getenv("TESTING", "").lower() in {"1", "true"} else Settings()
)

if not settings.REDIS_URL:
    logger.warning("REDIS_URL not configured; using in-process fallback cache")
