"""FastAPI 依赖：数据库会话、当前用户、权限校验。"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ErrorCode
from app.core.security import TOKEN_TYPE_ACCESS, decode_token
from app.db.session import get_db
from app.models.sys import SysUser
from app.services.auth import AuthService


__all__ = [
    "AccessTokenPayloadDep",
    "CurrentUserDep",
    "DbDep",
    "get_current_user",
    "get_db",
    "require_perm",
]

bearer_scheme = HTTPBearer(auto_error=False)

DbDep = Annotated[AsyncSession, Depends(get_db)]


async def get_access_payload(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> dict[str, Any]:
    """解析 Bearer 令牌并校验 type=access，返回 JWT 声明集合。"""
    if credentials is None or not credentials.credentials:
        raise AppException(ErrorCode.UNAUTHORIZED, "未提供认证凭证")
    payload = decode_token(credentials.credentials)
    if payload.get("type") != TOKEN_TYPE_ACCESS:
        raise AppException(ErrorCode.UNAUTHORIZED, "认证凭证无效")
    return payload


AccessTokenPayloadDep = Annotated[dict[str, Any], Depends(get_access_payload)]


async def get_current_user(payload: AccessTokenPayloadDep, db: DbDep) -> SysUser:
    """解析 Bearer 令牌并返回当前登录用户（校验 ver 匹配与 jti 未撤销）。"""
    return await AuthService(db).resolve_access_user(payload)


CurrentUserDep = Annotated[SysUser, Depends(get_current_user)]


def require_perm(perm_code: str) -> Callable[..., Awaitable[SysUser]]:
    """权限依赖工厂：require_perm("sys:user:list")。"""

    async def _checker(current_user: CurrentUserDep, db: DbDep) -> SysUser:
        codes = await AuthService(db).get_permission_codes(current_user.id)
        if "*" not in codes and perm_code not in codes:
            raise AppException(ErrorCode.FORBIDDEN, f"缺少权限: {perm_code}")
        return current_user

    return _checker
