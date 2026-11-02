import os

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # 默认值仅作占位，真实连接串写在 backend/.env（已被 .gitignore 忽略），避免口令入库
    DATABASE_URL: str = "postgresql+asyncpg://erp:change_me@127.0.0.1:5432/erp"

    SECRET_KEY: str = "change_me"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 10080
    JWT_ALGORITHM: str = "HS256"

    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    REDIS_URL: str | None = None

    LOG_LEVEL: str = "INFO"

    DEBUG: bool = True

    # 测试环境开关：为 True 时数据库连接池使用 NullPool
    # （测试每个用例独立事件循环，池化连接跨循环复用会报 'NoneType' object has no attribute 'send'）
    TESTING: bool = False


# 测试只使用显式环境配置，不读取开发机的 .env。
settings = (
    Settings(_env_file=None) if os.getenv("TESTING", "").lower() in {"1", "true"} else Settings()
)
