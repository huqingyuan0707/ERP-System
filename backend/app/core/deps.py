"""FastAPI 依赖：数据库会话、当前用户、权限校验。"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException, ErrorCode
from app.core.security import TOKEN_TYPE_ACCESS, decode_token
from app.db.session import get_db
from app.models.sys import SysUser
from app.services.auth import AuthService

try:
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover - Redis 未安装时降级
    Redis = None  # type: ignore[assignment]


__all__ = [
    "AccessTokenPayloadDep",
    "CurrentUserDep",
    "DbDep",
    "TenantDep",
    "get_current_tenant",
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


async def get_current_tenant(
    request: Request,
    payload: AccessTokenPayloadDep,
) -> int:
    """从请求头 X-Tenant-Id 或 JWT 载荷中解析当前租户 ID，缺失时抛出 400。

    优先级：请求头 > JWT 载荷。前端请求拦截器会统一注入 X-Tenant-Id，
    后端网关或测试脚本可通过该头指定租户上下文。
    """
    tenant_id = request.headers.get("X-Tenant-Id")
    if tenant_id is not None:
        return int(tenant_id)
    tenant_id = payload.get("tenant_id")
    if tenant_id is None:
        raise AppException(ErrorCode.BAD_REQUEST, "缺少租户上下文")
    return int(tenant_id)


TenantDep = Annotated[int, Depends(get_current_tenant)]


async def get_redis() -> Redis | None:
    """Redis 客户端依赖，用于幂等校验与缓存；未配置 REDIS_URL 时返回 None。"""
    if Redis is None or not settings.REDIS_URL:
        return None
    return Redis.from_url(settings.REDIS_URL, decode_responses=True)
