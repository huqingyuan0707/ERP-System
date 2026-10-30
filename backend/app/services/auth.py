"""认证业务逻辑。"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException, ErrorCode
from app.core.security import (
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.sys import SysPermission, SysUser
from app.repositories.login_log import LoginLogRepository
from app.repositories.revoked_token import RevokedTokenRepository
from app.repositories.user import UserRepository
from app.schemas.auth import CurrentUserOut, MenuOut, TokenOut


# 限流：同一 ip+username 最近 60 秒内失败 >= 5 次则拒绝
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_FAILURES = 5
# 锁定：同一 username 最近连续 5 条失败且最早一条在 15 分钟内则拒绝
LOCKOUT_FAILURE_COUNT = 5
LOCKOUT_MINUTES = 15

# 新密码强度：至少 8 位且同时包含字母和数字
_PASSWORD_PATTERN = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{8,}$")


@dataclass
class TokenPair:
    """令牌对：访问令牌出参 + 刷新令牌（用于写入 cookie）。"""

    token: TokenOut
    refresh_token: str
    refresh_max_age: int


def _to_aware(value: datetime) -> datetime:
    """SQLite 读回的 datetime 无时区，按 UTC 补齐后再比较。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _exp_to_datetime(exp: object, fallback_minutes: int) -> datetime:
    """将 JWT exp 声明转换为撤销表的时间戳，缺失时按签发时长兜底。"""
    if isinstance(exp, int | float):
        return datetime.fromtimestamp(float(exp), tz=UTC)
    return datetime.now(UTC) + timedelta(minutes=fallback_minutes)


def _build_menu_tree(nodes: list[SysPermission]) -> list[MenuOut]:
    """将目录/菜单节点按 sort 组装为树，父节点不在集合内的挂到根。"""
    node_ids = {node.id for node in nodes}
    children_map: dict[int, list[SysPermission]] = {}
    roots: list[SysPermission] = []
    for node in nodes:
        parent_id = node.parent_id if node.parent_id in node_ids else None
        if parent_id is None:
            roots.append(node)
        else:
            children_map.setdefault(parent_id, []).append(node)

    def _build(parent: SysPermission | None) -> list[MenuOut]:
        siblings = roots if parent is None else children_map.get(parent.id, [])
        return [
            MenuOut(
                id=node.id,
                name=node.name,
                route_path=node.route_path,
                icon=node.icon,
                children=_build(node),
            )
            for node in sorted(siblings, key=lambda item: (item.sort, item.id))
        ]

    return _build(None)


class AuthService:
    """登录、令牌签发/刷新/撤销、改密与当前用户信息。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.login_logs = LoginLogRepository(db)
        self.revoked = RevokedTokenRepository(db)

    async def login(
        self,
        username: str,
        password: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> TokenPair:
        """登录：限流/锁定检查 → 密码校验 → 写日志 → 签发令牌对。"""
        await self._ensure_login_allowed(username, ip, user_agent)
        user = await self.users.get_by_username(username)
        if user is None or not verify_password(password, user.password_hash):
            await self._write_login_log(username, ip, user_agent, False, "用户名或密码错误")
            await self.db.commit()
            raise AppException(ErrorCode.UNAUTHORIZED, "用户名或密码错误")
        if user.status != 1:
            await self._write_login_log(username, ip, user_agent, False, "该账号已被禁用")
            await self.db.commit()
            raise AppException(ErrorCode.FORBIDDEN, "该账号已被禁用")
        user.last_login_at = datetime.now(UTC)
        user.last_login_ip = ip
        pair = self._issue_pair(user)
        await self._write_login_log(username, ip, user_agent, True, "登录成功")
        await self.db.commit()
        return pair

    async def refresh(self, refresh_token: str | None) -> TokenPair:
        """校验刷新令牌并轮换出新的令牌对。"""
        if not refresh_token:
            raise AppException(ErrorCode.UNAUTHORIZED, "缺少刷新令牌")
        payload = decode_token(refresh_token)
        if payload.get("type") != TOKEN_TYPE_REFRESH:
            raise AppException(ErrorCode.UNAUTHORIZED, "无效的刷新令牌")
        jti = str(payload.get("jti") or "")
        if not jti:
            raise AppException(ErrorCode.UNAUTHORIZED, "无效的刷新令牌")
        if await self.revoked.exists_by_jti(jti):
            raise AppException(ErrorCode.UNAUTHORIZED, "刷新令牌已失效，请重新登录")
        user = await self._resolve_user(payload)
        pair = self._issue_pair(user)
        # 轮换：旧 refresh token 立即作废
        await self.revoked.create(
            jti, _exp_to_datetime(payload.get("exp"), settings.REFRESH_TOKEN_EXPIRE_MINUTES)
        )
        await self.db.commit()
        return pair

    async def logout(self, access_payload: dict[str, Any], refresh_token: str | None) -> None:
        """撤销当前 access 与 refresh 的 jti（refresh 无效时忽略，不影响登出）。"""
        access_jti = str(access_payload.get("jti") or "")
        if access_jti:
            await self.revoked.create(
                access_jti,
                _exp_to_datetime(
                    access_payload.get("exp"), settings.ACCESS_TOKEN_EXPIRE_MINUTES
                ),
            )
        if refresh_token:
            try:
                payload = decode_token(refresh_token)
            except AppException:
                payload = {}
            refresh_jti = str(payload.get("jti") or "")
            if payload.get("type") == TOKEN_TYPE_REFRESH and refresh_jti:
                await self.revoked.create(
                    refresh_jti,
                    _exp_to_datetime(
                        payload.get("exp"), settings.REFRESH_TOKEN_EXPIRE_MINUTES
                    ),
                )
        await self.db.commit()

    async def change_password(self, user: SysUser, old_password: str, new_password: str) -> None:
        """修改密码：校验旧密码与新密码强度，成功后令牌版本 +1 使旧 Token 失效。"""
        if not verify_password(old_password, user.password_hash):
            raise AppException(ErrorCode.VALIDATION_ERROR, "旧密码错误")
        if not _PASSWORD_PATTERN.fullmatch(new_password):
            raise AppException(
                ErrorCode.VALIDATION_ERROR, "新密码强度不足：至少8位且需同时包含字母和数字"
            )
        user.password_hash = hash_password(new_password)
        user.token_version += 1
        await self.db.commit()

    async def resolve_access_user(self, payload: dict[str, Any]) -> SysUser:
        """根据 access token 声明解析当前用户：校验 ver 匹配、jti 未撤销、账号可用。"""
        user = await self._resolve_user(payload)
        if user.status != 1:
            raise AppException(ErrorCode.FORBIDDEN, "该账号已被禁用")
        jti = str(payload.get("jti") or "")
        if jti and await self.revoked.exists_by_jti(jti):
            raise AppException(ErrorCode.UNAUTHORIZED, "登录状态已失效，请重新登录")
        return user

    async def get_permission_codes(self, user_id: int) -> list[str]:
        return await self.users.list_permission_codes(user_id)

    async def get_me(self, user: SysUser) -> CurrentUserOut:
        """当前用户信息：角色、权限码与可见菜单树。"""
        roles = await self.users.list_role_codes(user.id)
        permissions = await self.get_permission_codes(user.id)
        menus = _build_menu_tree(await self.users.list_visible_menus(user.id))
        return CurrentUserOut(
            id=user.id,
            username=user.username,
            real_name=user.real_name,
            roles=roles,
            permissions=permissions,
            menus=menus,
        )

    async def _ensure_login_allowed(
        self, username: str, ip: str | None, user_agent: str | None
    ) -> None:
        """登录限流与锁定检查，须在密码校验之前执行。"""
        failures = await self.login_logs.count_failures_since(
            username, ip, RATE_LIMIT_WINDOW_SECONDS
        )
        if failures >= RATE_LIMIT_MAX_FAILURES:
            await self._write_login_log(username, ip, user_agent, False, "请求过于频繁被限制")
            await self.db.commit()
            raise AppException(ErrorCode.TOO_MANY_REQUESTS, "尝试过于频繁，请稍后再试")
        recent = await self.login_logs.list_recent(username, LOCKOUT_FAILURE_COUNT)
        if (
            len(recent) == LOCKOUT_FAILURE_COUNT
            and all(item.status == 0 for item in recent)
            and recent[-1].created_at is not None
            and _to_aware(recent[-1].created_at)
            >= datetime.now(UTC) - timedelta(minutes=LOCKOUT_MINUTES)
        ):
            await self._write_login_log(username, ip, user_agent, False, "失败次数过多被锁定")
            await self.db.commit()
            raise AppException(
                ErrorCode.TOO_MANY_REQUESTS, "失败次数过多，账号已被临时锁定，请稍后再试"
            )

    async def _write_login_log(
        self, username: str, ip: str | None, user_agent: str | None, success: bool, message: str
    ) -> None:
        """记录一次登录尝试（status: 1 成功 / 0 失败）。"""
        user_agent = user_agent[:512] if user_agent else None
        await self.login_logs.create(
            username=username,
            ip=ip,
            user_agent=user_agent,
            status=1 if success else 0,
            message=message,
        )

    async def _resolve_user(self, payload: dict[str, Any]) -> SysUser:
        """按声明解析用户：校验存在性与令牌版本号。"""
        subject = payload.get("sub")
        ver = payload.get("ver")
        if subject is None or ver is None:
            raise AppException(ErrorCode.UNAUTHORIZED, "认证凭证无效")
        user = await self.db.get(SysUser, int(str(subject)))
        if user is None or user.is_deleted:
            raise AppException(ErrorCode.UNAUTHORIZED, "用户不存在或已被删除")
        if int(str(ver)) != user.token_version:
            raise AppException(ErrorCode.UNAUTHORIZED, "登录状态已失效，请重新登录")
        return user

    def _issue_pair(self, user: SysUser) -> TokenPair:
        """为用户签发 access + refresh 令牌对。"""
        access_token, expires_in = create_access_token(user.id, user.token_version)
        refresh_token, _ = create_refresh_token(user.id, user.token_version)
        return TokenPair(
            token=TokenOut(access_token=access_token, token_type="bearer", expires_in=expires_in),
            refresh_token=refresh_token,
            refresh_max_age=settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
        )
