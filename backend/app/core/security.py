"""密码哈希与 JWT 签发/校验。

说明：passlib 1.7.4 已停止维护，且与 bcrypt>=5 不兼容（后端探测即抛
ValueError: password cannot be longer than 72 bytes），因此直接使用 bcrypt 库。
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings
from app.core.exceptions import AppException, ErrorCode


# bcrypt 仅使用前 72 字节，超长部分按历史语义截断
_BCRYPT_MAX_BYTES = 72

# JWT type 声明取值
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"


def _to_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_to_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_to_bytes(plain_password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _create_token(
    subject: str | int, token_type: str, token_version: int, expires_minutes: int
) -> tuple[str, int]:
    """签发令牌，claims 含 sub/ver/jti/type/iat/exp，返回 (token, 有效期秒数)。"""
    now = datetime.now(UTC)
    payload = {
        "sub": str(subject),
        "ver": token_version,
        "jti": uuid4().hex,
        "type": token_type,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, expires_minutes * 60


def create_access_token(
    subject: str | int, token_version: int, expires_minutes: int | None = None
) -> tuple[str, int]:
    """签发访问令牌，返回 (token, 有效期秒数)。"""
    minutes = (
        expires_minutes if expires_minutes is not None else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    return _create_token(subject, TOKEN_TYPE_ACCESS, token_version, minutes)


def create_refresh_token(
    subject: str | int, token_version: int, expires_minutes: int | None = None
) -> tuple[str, int]:
    """签发刷新令牌，返回 (token, 有效期秒数)。"""
    minutes = (
        expires_minutes if expires_minutes is not None else settings.REFRESH_TOKEN_EXPIRE_MINUTES
    )
    return _create_token(subject, TOKEN_TYPE_REFRESH, token_version, minutes)


def decode_token(token: str) -> dict[str, object]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise AppException(ErrorCode.UNAUTHORIZED, "登录状态已失效，请重新登录") from exc
