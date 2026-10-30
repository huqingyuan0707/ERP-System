"""认证模块接口测试：登录/限流/锁定/刷新/登出/改密/当前用户。"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from jose import jwt as jose_jwt
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.sys import SysLoginLog, SysUser, SysUserRole


AUTH = "/api/v1/auth"
PASSWORD = "Passw0rd123"


def _new_username() -> str:
    """每次运行生成唯一用户名，避免历史登录日志干扰限流/锁定统计。"""
    return "t" + uuid.uuid4().hex[:12]


def _run(coro: Coroutine[Any, Any, None]) -> None:
    asyncio.run(coro)


@asynccontextmanager
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _create_user(username: str, password: str = PASSWORD, status: int = 1) -> int:
    """直接落库创建测试用户，返回用户 id。"""
    async with AsyncSessionLocal() as session:
        user = SysUser(
            username=username,
            password_hash=hash_password(password),
            real_name="测试用户",
            status=status,
        )
        session.add(user)
        await session.commit()
        return int(user.id)


async def _cleanup_user(username: str) -> None:
    """清理测试用户、角色关联与登录日志。"""
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(SysUser).where(SysUser.username == username))
        ).scalar_one_or_none()
        if user is not None:
            await session.execute(delete(SysUserRole).where(SysUserRole.user_id == user.id))
            await session.execute(delete(SysUser).where(SysUser.id == user.id))
        await session.execute(delete(SysLoginLog).where(SysLoginLog.username == username))
        await session.commit()


async def _list_login_logs(username: str) -> list[SysLoginLog]:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(SysLoginLog)
            .where(SysLoginLog.username == username)
            .order_by(SysLoginLog.id)
        )
        return list((await session.execute(stmt)).scalars().all())


async def _insert_failure_logs(username: str, minutes_ago_list: list[int]) -> None:
    """按指定时间插入历史失败登录日志（用于锁定规则的滑动窗口）。"""
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        for minutes in minutes_ago_list:
            session.add(
                SysLoginLog(
                    username=username,
                    ip="127.0.0.1",
                    user_agent="pytest",
                    status=0,
                    message="用户名或密码错误",
                    created_at=now - timedelta(minutes=minutes),
                    updated_at=now - timedelta(minutes=minutes),
                )
            )
        await session.commit()


async def _login(client: httpx.AsyncClient, username: str, password: str) -> httpx.Response:
    return await client.post(f"{AUTH}/login", json={"username": username, "password": password})


def _extract_refresh_cookie(response: httpx.Response) -> str:
    for cookie in response.headers.get_list("set-cookie"):
        if cookie.startswith("refresh_token="):
            return cookie.split(";", 1)[0].removeprefix("refresh_token=")
    raise AssertionError("refresh_token cookie 不存在")


def _make_refresh_token(user_id: int, token_version: int, expired: bool) -> str:
    """构造 refresh token：expired=True 时 exp 已过期。"""
    now = datetime.now(UTC)
    exp = now - timedelta(minutes=5) if expired else now + timedelta(minutes=30)
    payload = {
        "sub": str(user_id),
        "ver": token_version,
        "jti": uuid.uuid4().hex,
        "type": "refresh",
        "exp": exp,
    }
    return jose_jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def test_login_success() -> None:
    async def scenario() -> None:
        username = _new_username()
        await _create_user(username)
        try:
            async with _client() as client:
                resp = await _login(client, username, PASSWORD)
                assert resp.status_code == 200
                body = resp.json()
                assert body["code"] == 0
                data = body["data"]
                assert data["accessToken"]
                assert data["tokenType"] == "bearer"
                assert data["expiresIn"] == 120 * 60
                cookies = resp.headers.get_list("set-cookie")
                refresh_cookie = next(c for c in cookies if c.startswith("refresh_token="))
                lowered = refresh_cookie.lower()
                assert "httponly" in lowered
                assert "samesite=lax" in lowered
                assert "path=/api/v1/auth" in lowered
                assert "max-age=604800" in lowered
                # 成功日志与 last_login 更新
                logs = await _list_login_logs(username)
                assert any(log.status == 1 and log.message == "登录成功" for log in logs)
                async with AsyncSessionLocal() as session:
                    user = (
                        await session.execute(
                            select(SysUser).where(SysUser.username == username)
                        )
                    ).scalar_one()
                    assert user.last_login_at is not None
                    assert user.last_login_ip == "127.0.0.1"
        finally:
            await _cleanup_user(username)

    _run(scenario())


def test_login_wrong_password_returns_401_and_logs() -> None:
    async def scenario() -> None:
        username = _new_username()
        await _create_user(username)
        try:
            async with _client() as client:
                resp = await _login(client, username, "WrongPass1")
                assert resp.status_code == 401
                assert resp.json()["code"] == 401
                logs = await _list_login_logs(username)
                assert any(
                    log.status == 0 and "用户名或密码错误" in (log.message or "")
                    for log in logs
                )
        finally:
            await _cleanup_user(username)

    _run(scenario())


def test_login_disabled_returns_403_and_logs() -> None:
    async def scenario() -> None:
        username = _new_username()
        await _create_user(username, status=0)
        try:
            async with _client() as client:
                resp = await _login(client, username, PASSWORD)
                assert resp.status_code == 403
                assert resp.json()["code"] == 403
                logs = await _list_login_logs(username)
                assert any(
                    log.status == 0 and "禁用" in (log.message or "") for log in logs
                )
        finally:
            await _cleanup_user(username)

    _run(scenario())


def test_login_rate_limit_after_five_failures() -> None:
    async def scenario() -> None:
        username = _new_username()
        await _create_user(username)
        try:
            async with _client() as client:
                for _ in range(5):
                    resp = await _login(client, username, "WrongPass1")
                    assert resp.status_code == 401
                blocked = await _login(client, username, "WrongPass1")
                assert blocked.status_code == 429
                assert blocked.json()["code"] == 429
        finally:
            await _cleanup_user(username)

    _run(scenario())


def test_login_lockout_by_consecutive_failures() -> None:
    """历史 5 连败（均超过 60 秒窗口、最早一条在 15 分钟内）→ 锁定 429。"""

    async def scenario() -> None:
        username = _new_username()
        await _create_user(username)
        try:
            # 失败时间间隔大于限流窗口，只触发锁定规则
            await _insert_failure_logs(username, [2, 4, 6, 8, 10])
            async with _client() as client:
                resp = await _login(client, username, PASSWORD)
                assert resp.status_code == 429
                assert resp.json()["code"] == 429
        finally:
            await _cleanup_user(username)

    _run(scenario())


def test_refresh_rotates_tokens() -> None:
    async def scenario() -> None:
        username = _new_username()
        await _create_user(username)
        try:
            async with _client() as client:
                resp = await _login(client, username, PASSWORD)
                old_access = resp.json()["data"]["accessToken"]
                old_refresh = _extract_refresh_cookie(resp)
                # cookie 自动携带
                refreshed = await client.post(f"{AUTH}/refresh")
                assert refreshed.status_code == 200
                body = refreshed.json()
                assert body["code"] == 0
                assert body["data"]["accessToken"]
                assert body["data"]["accessToken"] != old_access
                new_refresh = _extract_refresh_cookie(refreshed)
                assert new_refresh != old_refresh
            # 轮换后旧 refresh 已撤销
            async with _client() as client2:
                resp = await client2.post(
                    f"{AUTH}/refresh", json={"refreshToken": old_refresh}
                )
                assert resp.status_code == 401
            # 新 refresh 仍可用（用 body 方式提交）
            async with _client() as client3:
                resp = await client3.post(f"{AUTH}/refresh", json={"refreshToken": new_refresh})
                assert resp.status_code == 200
        finally:
            await _cleanup_user(username)

    _run(scenario())


def test_refresh_tampered_or_expired_returns_401() -> None:
    async def scenario() -> None:
        username = _new_username()
        user_id = await _create_user(username)
        try:
            async with _client() as client:
                resp = await _login(client, username, PASSWORD)
                access = resp.json()["data"]["accessToken"]
                # 篡改 token
                resp = await client.post(f"{AUTH}/refresh", json={"refreshToken": access + "x"})
                assert resp.status_code == 401
                # access token 不能当 refresh 用
                resp = await client.post(f"{AUTH}/refresh", json={"refreshToken": access})
                assert resp.status_code == 401
                # 过期 refresh token
                expired = _make_refresh_token(user_id, 0, expired=True)
                resp = await client.post(f"{AUTH}/refresh", json={"refreshToken": expired})
                assert resp.status_code == 401
            # 缺失 refresh token（全新 client，无 cookie、无 body）
            async with _client() as fresh:
                resp = await fresh.post(f"{AUTH}/refresh")
                assert resp.status_code == 401
        finally:
            await _cleanup_user(username)

    _run(scenario())


def test_logout_revokes_access_and_refresh() -> None:
    async def scenario() -> None:
        username = _new_username()
        await _create_user(username)
        try:
            async with _client() as client:
                resp = await _login(client, username, PASSWORD)
                access = resp.json()["data"]["accessToken"]
                old_refresh = _extract_refresh_cookie(resp)
                headers = {"Authorization": f"Bearer {access}"}
                logout_resp = await client.post(f"{AUTH}/logout", headers=headers)
                assert logout_resp.status_code == 200
                assert logout_resp.json()["code"] == 0
                cleared = [
                    c
                    for c in logout_resp.headers.get_list("set-cookie")
                    if c.startswith("refresh_token=")
                ]
                assert cleared and "max-age=0" in cleared[0].lower()
                # 原 access 不可用
                me_resp = await client.get(f"{AUTH}/me", headers=headers)
                assert me_resp.status_code == 401
                # 原 refresh 不可用
                refresh_resp = await client.post(
                    f"{AUTH}/refresh", json={"refreshToken": old_refresh}
                )
                assert refresh_resp.status_code == 401
        finally:
            await _cleanup_user(username)

    _run(scenario())


def test_change_password_wrong_old_returns_422() -> None:
    async def scenario() -> None:
        username = _new_username()
        await _create_user(username)
        try:
            async with _client() as client:
                resp = await _login(client, username, PASSWORD)
                access = resp.json()["data"]["accessToken"]
                resp = await client.post(
                    f"{AUTH}/password",
                    headers={"Authorization": f"Bearer {access}"},
                    json={"oldPassword": "WrongOld1", "newPassword": "NewPassw0rd456"},
                )
                assert resp.status_code == 422
                assert resp.json()["code"] == 422
        finally:
            await _cleanup_user(username)

    _run(scenario())


def test_change_password_weak_returns_422() -> None:
    async def scenario() -> None:
        username = _new_username()
        await _create_user(username)
        try:
            async with _client() as client:
                resp = await _login(client, username, PASSWORD)
                access = resp.json()["data"]["accessToken"]
                headers = {"Authorization": f"Bearer {access}"}
                for weak in ("Sh1", "12345678", "abcdefgh"):
                    resp = await client.post(
                        f"{AUTH}/password",
                        headers=headers,
                        json={"oldPassword": PASSWORD, "newPassword": weak},
                    )
                    assert resp.status_code == 422
        finally:
            await _cleanup_user(username)

    _run(scenario())


def test_change_password_success_invalidates_old_tokens() -> None:
    async def scenario() -> None:
        username = _new_username()
        new_password = "NewPassw0rd456"
        await _create_user(username)
        try:
            async with _client() as client:
                resp = await _login(client, username, PASSWORD)
                access = resp.json()["data"]["accessToken"]
                headers = {"Authorization": f"Bearer {access}"}
                resp = await client.post(
                    f"{AUTH}/password",
                    headers=headers,
                    json={"oldPassword": PASSWORD, "newPassword": new_password},
                )
                assert resp.status_code == 200
                assert resp.json()["code"] == 0
                # 旧 access 全部失效
                me_resp = await client.get(f"{AUTH}/me", headers=headers)
                assert me_resp.status_code == 401
                # 旧密码不能再登录
                old_login = await _login(client, username, PASSWORD)
                assert old_login.status_code == 401
                # 新密码可登录
                new_login = await _login(client, username, new_password)
                assert new_login.status_code == 200
        finally:
            await _cleanup_user(username)

    _run(scenario())


def test_me_returns_roles_permissions_menus() -> None:
    async def scenario() -> None:
        async with _client() as client:
            resp = await _login(client, "admin", "admin123")
            assert resp.status_code == 200
            access = resp.json()["data"]["accessToken"]
            me_resp = await client.get(f"{AUTH}/me", headers={"Authorization": f"Bearer {access}"})
            assert me_resp.status_code == 200
            data = me_resp.json()["data"]
            assert data["username"] == "admin"
            assert "admin" in data["roles"]
            assert data["permissions"]
            menus = data["menus"]
            assert isinstance(menus, list) and menus
            node = menus[0]
            for key in ("id", "name", "routePath", "icon", "children"):
                assert key in node
            sys_dir = next((m for m in menus if m["routePath"] == "/system"), None)
            assert sys_dir is not None
            child_paths = [child["routePath"] for child in sys_dir["children"]]
            assert "/system/user" in child_paths

    _run(scenario())
