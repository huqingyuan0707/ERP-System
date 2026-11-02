"""配置环境隔离与生产安全校验。"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


PRODUCTION_DATABASE_URL = "postgresql+asyncpg://erp:strong-db-password@postgres:5432/erp"
PRODUCTION_SECRET_KEY = "0123456789abcdef0123456789abcdef"
PRODUCTION_ADMIN_PASSWORD = "AdminPassword2026"


def build_settings(**overrides: object) -> Settings:
    """构造完全显式的 Settings，避免读取开发机环境或 .env。"""
    values: dict[str, object] = {
        "ENVIRONMENT": "development",
        "TESTING": False,
        "DATABASE_URL": "sqlite+aiosqlite:///./local-test.db",
        "SECRET_KEY": "development-only-secret",
        "INITIAL_ADMIN_PASSWORD": None,
        "REDIS_URL": None,
        "DEBUG": None,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": PRODUCTION_DATABASE_URL,
        "SECRET_KEY": PRODUCTION_SECRET_KEY,
        "INITIAL_ADMIN_PASSWORD": PRODUCTION_ADMIN_PASSWORD,
    }
    values.update(overrides)
    return build_settings(**values)


def test_development_keeps_compatible_placeholders_and_debug_default() -> None:
    config = build_settings(
        DATABASE_URL="postgresql+asyncpg://erp:change_me@127.0.0.1:5432/erp",
        SECRET_KEY="change_me",
    )

    assert config.ENVIRONMENT == "development"
    assert config.DEBUG is True
    assert config.INITIAL_ADMIN_PASSWORD is None


def test_development_respects_explicit_debug_value() -> None:
    config = build_settings(DEBUG=False)

    assert config.DEBUG is False


@pytest.mark.parametrize(
    ("environment", "testing"),
    [("test", False), ("development", True)],
)
def test_isolated_test_modes_allow_fixed_secret_and_sqlite(
    environment: str, testing: bool
) -> None:
    config = build_settings(
        ENVIRONMENT=environment,
        TESTING=testing,
        DATABASE_URL="sqlite+aiosqlite:///./isolated.sqlite3",
        SECRET_KEY="fixed-isolated-test-key",
    )

    assert config.DATABASE_URL.startswith("sqlite")
    assert config.SECRET_KEY == "fixed-isolated-test-key"
    assert config.DEBUG is (environment == "development" and not testing)


def test_production_accepts_secure_configuration_and_empty_redis() -> None:
    config = production_settings(REDIS_URL="")

    assert config.ENVIRONMENT == "production"
    assert config.DEBUG is False
    assert config.REDIS_URL == ""
    assert config.INITIAL_ADMIN_PASSWORD == PRODUCTION_ADMIN_PASSWORD


def test_production_respects_explicit_debug_value() -> None:
    config = production_settings(DEBUG=True)

    assert config.DEBUG is True


@pytest.mark.parametrize(
    "secret_key",
    [
        "",
        "change_me",
        "dev-secret-key",
        "test-secret-key",
        "isolated-test-secret-not-for-deployment",
        "short-but-not-placeholder",
    ],
)
def test_production_rejects_insecure_secret_key(secret_key: str) -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        production_settings(SECRET_KEY=secret_key)


@pytest.mark.parametrize(
    "database_url",
    [
        "",
        "postgresql+asyncpg://erp:change_me@postgres:5432/erp",
        "sqlite+aiosqlite:///./production.db",
        "sqlite:///./production.db",
    ],
)
def test_production_rejects_insecure_database_url(database_url: str) -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        production_settings(DATABASE_URL=database_url)


@pytest.mark.parametrize(
    "admin_password",
    [None, "", "short1", "onlyletters", "12345678"],
)
def test_production_rejects_missing_or_weak_initial_admin_password(
    admin_password: str | None,
) -> None:
    with pytest.raises(ValidationError, match="INITIAL_ADMIN_PASSWORD"):
        production_settings(INITIAL_ADMIN_PASSWORD=admin_password)
