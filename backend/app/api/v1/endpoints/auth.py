"""认证端点。"""

from fastapi import APIRouter, Request, Response

from app.core.config import settings
from app.core.deps import AccessTokenPayloadDep, CurrentUserDep, DbDep
from app.schemas.auth import (
    CurrentUserOut,
    LoginRequest,
    LogoutRequest,
    PasswordChangeRequest,
    RefreshRequest,
    TokenOut,
)
from app.schemas.common import ApiResponse, success_response
from app.services.auth import AuthService, TokenPair


router = APIRouter(prefix="/auth", tags=["认证"])

_REFRESH_COOKIE_NAME = "refresh_token"
_REFRESH_COOKIE_PATH = f"{settings.API_V1_PREFIX}/auth"


def _set_refresh_cookie(response: Response, pair: TokenPair) -> None:
    """将 refresh token 写入 httponly cookie（login/refresh 轮换时重设）。"""
    response.set_cookie(
        key=_REFRESH_COOKIE_NAME,
        value=pair.refresh_token,
        max_age=pair.refresh_max_age,
        httponly=True,
        samesite="lax",
        path=_REFRESH_COOKIE_PATH,
    )


def _pop_refresh_token(
    request: Request, payload: RefreshRequest | LogoutRequest | None
) -> str | None:
    """优先取 body 中的 refreshToken，其次取 cookie。"""
    from_payload = payload.refresh_token if payload is not None else None
    return from_payload or request.cookies.get(_REFRESH_COOKIE_NAME)


@router.post("/login", summary="登录")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DbDep,
) -> ApiResponse[TokenOut]:
    pair = await AuthService(db).login(
        payload.username,
        payload.password,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    _set_refresh_cookie(response, pair)
    return success_response(pair.token)


@router.post("/refresh", summary="刷新令牌")
async def refresh(
    request: Request,
    response: Response,
    db: DbDep,
    payload: RefreshRequest | None = None,
) -> ApiResponse[TokenOut]:
    """公开接口：从 cookie 或 body 取 refresh token，轮换出新令牌对。"""
    pair = await AuthService(db).refresh(_pop_refresh_token(request, payload))
    _set_refresh_cookie(response, pair)
    return success_response(pair.token)


@router.post("/logout", summary="登出")
async def logout(
    request: Request,
    response: Response,
    access_payload: AccessTokenPayloadDep,
    current_user: CurrentUserDep,
    db: DbDep,
    payload: LogoutRequest | None = None,
) -> ApiResponse[None]:
    refresh_token = _pop_refresh_token(request, payload)
    await AuthService(db).logout(access_payload, refresh_token)
    response.delete_cookie(
        key=_REFRESH_COOKIE_NAME, path=_REFRESH_COOKIE_PATH, httponly=True, samesite="lax"
    )
    return success_response()


@router.post("/password", summary="修改密码")
async def change_password(
    payload: PasswordChangeRequest,
    current_user: CurrentUserDep,
    db: DbDep,
) -> ApiResponse[None]:
    await AuthService(db).change_password(
        current_user, payload.old_password, payload.new_password
    )
    return success_response()


@router.get("/me", summary="当前用户信息")
async def get_me(current_user: CurrentUserDep, db: DbDep) -> ApiResponse[CurrentUserOut]:
    result = await AuthService(db).get_me(current_user)
    return success_response(result)
