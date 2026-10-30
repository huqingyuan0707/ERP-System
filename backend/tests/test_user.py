"""S-01 用户管理验收测试。

覆盖：新增用户并分配角色（登录后权限正确）、用户名重复 409、部门/角色不存在 422、
重置密码与禁用使旧 Token 失效、不能删除/禁用自己与 admin、删除为软删。
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import httpx
from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.sys import (
    SysDept,
    SysOperLog,
    SysRole,
    SysRolePermission,
    SysUser,
    SysUserRole,
)


API = "/api/v1"
ADMIN = ("admin", "admin123")
PASSWORD = "Passw0rd123"
NEW_PASSWORD = "NewPassw0rd456"


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


@asynccontextmanager
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login_token(client: httpx.AsyncClient, username: str, password: str) -> str:
    resp = await client.post(
        f"{API}/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["data"]["accessToken"])


async def _create_dept(name: str) -> int:
    async with AsyncSessionLocal() as session:
        dept = SysDept(name=name, sort=99, status=1)
        session.add(dept)
        await session.commit()
        return int(dept.id)


async def _create_role(code: str) -> int:
    async with AsyncSessionLocal() as session:
        role = SysRole(code=code, name="用户管理测试角色", data_scope=1, status=1)
        session.add(role)
        await session.commit()
        return int(role.id)


async def _create_user(
    client: httpx.AsyncClient,
    token: str,
    username: str,
    *,
    dept_id: int | None = None,
    role_ids: list[int] | None = None,
) -> httpx.Response:
    return await client.post(
        f"{API}/sys/users",
        headers=_auth(token),
        json={
            "username": username,
            "password": PASSWORD,
            "realName": "测试用户",
            "mobile": "13800000000",
            "deptId": dept_id,
            "position": "专员",
            "roleIds": role_ids or [],
        },
    )


async def _cleanup(
    username: str, role_id: int | None = None, dept_id: int | None = None
) -> None:
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(SysUser).where(SysUser.username == username))
        ).scalar_one_or_none()
        if user is not None:
            await session.execute(delete(SysUserRole).where(SysUserRole.user_id == user.id))
            await session.execute(delete(SysUser).where(SysUser.id == user.id))
        if role_id is not None:
            await session.execute(
                delete(SysRolePermission).where(SysRolePermission.role_id == role_id)
            )
            await session.execute(delete(SysRole).where(SysRole.id == role_id))
        if dept_id is not None:
            await session.execute(delete(SysDept).where(SysDept.id == dept_id))
        await session.commit()


async def _cleanup_oper_logs(module: str, action_prefix: str) -> None:
    """清理测试产生的操作日志，避免污染开发库。"""
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(SysOperLog).where(
                SysOperLog.module == module, SysOperLog.action.like(f"{action_prefix}%")
            )
        )
        await session.commit()


def test_create_user_with_role_then_login() -> None:
    """新增用户并分配角色：登录后角色正确，详情回显 roleIds 与最后登录时间。"""
    _run(_scenario_create_with_role())


async def _scenario_create_with_role() -> None:
    suffix = uuid.uuid4().hex[:8]
    username = f"u{suffix}"
    role_code = f"r{suffix}"
    dept_id = await _create_dept(f"部{suffix}")
    role_id = await _create_role(role_code)
    try:
        async with _client() as client:
            admin_token = await _login_token(client, *ADMIN)
            resp = await _create_user(
                client, admin_token, username, dept_id=dept_id, role_ids=[role_id]
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()["data"]
            assert data["roleIds"] == [role_id]
            assert data["deptId"] == dept_id
            user_id = int(data["id"])

            token = await _login_token(client, username, PASSWORD)
            me = await client.get(f"{API}/auth/me", headers=_auth(token))
            assert me.status_code == 200, me.text
            assert me.json()["data"]["roles"] == [role_code]

            detail = await client.get(
                f"{API}/sys/users/{user_id}", headers=_auth(admin_token)
            )
            assert detail.status_code == 200, detail.text
            detail_data = detail.json()["data"]
            assert detail_data["roleIds"] == [role_id]
            assert detail_data["lastLoginAt"] is not None

            listing = await client.get(
                f"{API}/sys/users", headers=_auth(admin_token), params={"keyword": username}
            )
            assert listing.status_code == 200, listing.text
            items = listing.json()["data"]["items"]
            assert any(item["username"] == username for item in items)
    finally:
        await _cleanup(username, role_id, dept_id)


def test_duplicate_username_and_invalid_refs() -> None:
    """用户名重复 409；部门/角色不存在 422（新增与修改都校验）。"""
    _run(_scenario_duplicate_and_invalid_refs())


async def _scenario_duplicate_and_invalid_refs() -> None:
    suffix = uuid.uuid4().hex[:8]
    username = f"u{suffix}"
    role_id = await _create_role(f"r{suffix}")
    try:
        async with _client() as client:
            admin_token = await _login_token(client, *ADMIN)
            first = await _create_user(client, admin_token, username, role_ids=[role_id])
            assert first.status_code == 200, first.text

            duplicate = await _create_user(client, admin_token, username)
            assert duplicate.status_code == 409, duplicate.text

            bad_dept = await _create_user(
                client, admin_token, f"d{suffix}", dept_id=999_999_999
            )
            assert bad_dept.status_code == 422, bad_dept.text

            bad_role = await _create_user(
                client, admin_token, f"r{suffix}u", role_ids=[999_999_999]
            )
            assert bad_role.status_code == 422, bad_role.text

            user_id = int(first.json()["data"]["id"])
            update_bad_role = await client.put(
                f"{API}/sys/users/{user_id}",
                headers=_auth(admin_token),
                json={"roleIds": [999_999_999]},
            )
            assert update_bad_role.status_code == 422, update_bad_role.text

            update_bad_dept = await client.put(
                f"{API}/sys/users/{user_id}",
                headers=_auth(admin_token),
                json={"deptId": 999_999_999},
            )
            assert update_bad_dept.status_code == 422, update_bad_dept.text
    finally:
        await _cleanup(username, role_id)


def test_reset_password_invalidates_old_token() -> None:
    """重置密码后：旧密码失败、旧 Token 失效、新密码可登录。"""
    _run(_scenario_reset_password())


async def _scenario_reset_password() -> None:
    suffix = uuid.uuid4().hex[:8]
    username = f"u{suffix}"
    try:
        async with _client() as client:
            admin_token = await _login_token(client, *ADMIN)
            created = await _create_user(client, admin_token, username)
            assert created.status_code == 200, created.text
            user_id = int(created.json()["data"]["id"])

            old_token = await _login_token(client, username, PASSWORD)
            reset = await client.post(
                f"{API}/sys/users/{user_id}/reset-password",
                headers=_auth(admin_token),
                json={"password": NEW_PASSWORD},
            )
            assert reset.status_code == 200, reset.text

            with_old_token = await client.get(f"{API}/auth/me", headers=_auth(old_token))
            assert with_old_token.status_code in (401, 403), with_old_token.text

            old_password = await client.post(
                f"{API}/auth/login", json={"username": username, "password": PASSWORD}
            )
            assert old_password.status_code == 401, old_password.text

            new_token = await _login_token(client, username, NEW_PASSWORD)
            me = await client.get(f"{API}/auth/me", headers=_auth(new_token))
            assert me.status_code == 200, me.text
    finally:
        await _cleanup(username)


def test_disable_user_blocks_login_and_token() -> None:
    """禁用用户后：登录 403，已有 Token 立即失效。"""
    _run(_scenario_disable_user())


async def _scenario_disable_user() -> None:
    suffix = uuid.uuid4().hex[:8]
    username = f"u{suffix}"
    try:
        async with _client() as client:
            admin_token = await _login_token(client, *ADMIN)
            created = await _create_user(client, admin_token, username)
            assert created.status_code == 200, created.text
            user_id = int(created.json()["data"]["id"])

            token = await _login_token(client, username, PASSWORD)
            disable = await client.put(
                f"{API}/sys/users/{user_id}",
                headers=_auth(admin_token),
                json={"status": 0},
            )
            assert disable.status_code == 200, disable.text

            with_old_token = await client.get(f"{API}/auth/me", headers=_auth(token))
            assert with_old_token.status_code in (401, 403), with_old_token.text

            login = await client.post(
                f"{API}/auth/login", json={"username": username, "password": PASSWORD}
            )
            assert login.status_code == 403, login.text
    finally:
        await _cleanup(username)


def test_cannot_disable_or_delete_self_or_admin() -> None:
    """不能删除/禁用当前登录用户与内置 admin 账号。"""
    _run(_scenario_protect_self_and_admin())


async def _scenario_protect_self_and_admin() -> None:
    suffix = uuid.uuid4().hex[:8]
    username = f"u{suffix}"
    try:
        async with _client() as client:
            admin_token = await _login_token(client, *ADMIN)
            me = await client.get(f"{API}/auth/me", headers=_auth(admin_token))
            assert me.status_code == 200, me.text
            admin_id = int(me.json()["data"]["id"])

            created = await _create_user(client, admin_token, username)
            assert created.status_code == 200, created.text
            user_id = int(created.json()["data"]["id"])

            # admin 既是"当前登录用户"又是"内置管理员账号"，两条保护同时生效
            delete_admin = await client.delete(
                f"{API}/sys/users/{admin_id}", headers=_auth(admin_token)
            )
            assert delete_admin.status_code == 400, delete_admin.text

            disable_admin = await client.put(
                f"{API}/sys/users/{admin_id}",
                headers=_auth(admin_token),
                json={"status": 0},
            )
            assert disable_admin.status_code == 400, disable_admin.text

            # 其他普通用户可以正常删除，不受保护规则影响
            delete_other = await client.delete(
                f"{API}/sys/users/{user_id}", headers=_auth(admin_token)
            )
            assert delete_other.status_code == 200, delete_other.text
    finally:
        await _cleanup(username)


def test_write_oper_log_for_key_operations() -> None:
    """新增/修改/删除/重置密码均写入 sys_oper_log 操作日志。"""
    _run(_scenario_oper_log())


async def _scenario_oper_log() -> None:
    suffix = uuid.uuid4().hex[:8]
    username = f"u{suffix}"
    try:
        async with _client() as client:
            admin_token = await _login_token(client, *ADMIN)
            created = await _create_user(client, admin_token, username)
            assert created.status_code == 200, created.text
            user_id = int(created.json()["data"]["id"])

            updated = await client.put(
                f"{API}/sys/users/{user_id}",
                headers=_auth(admin_token),
                json={"realName": "改名"},
            )
            assert updated.status_code == 200, updated.text

            reset = await client.post(
                f"{API}/sys/users/{user_id}/reset-password",
                headers=_auth(admin_token),
                json={"password": NEW_PASSWORD},
            )
            assert reset.status_code == 200, reset.text

            deleted = await client.delete(
                f"{API}/sys/users/{user_id}", headers=_auth(admin_token)
            )
            assert deleted.status_code == 200, deleted.text

            async with AsyncSessionLocal() as session:
                stmt = select(SysOperLog.action).where(
                    SysOperLog.module == "系统管理",
                    SysOperLog.action.in_(["新增用户", "修改用户", "删除用户", "重置密码"]),
                )
                actions = set((await session.execute(stmt)).scalars().all())
            assert actions == {"新增用户", "修改用户", "删除用户", "重置密码"}, actions
    finally:
        await _cleanup(username)
        await _cleanup_oper_logs("系统管理", "新增用户")


def test_delete_user_is_soft_delete() -> None:
    """删除用户为软删除：列表不再展示，数据库记录仍保留以便追溯历史单据。"""
    _run(_scenario_soft_delete())


async def _scenario_soft_delete() -> None:
    suffix = uuid.uuid4().hex[:8]
    username = f"u{suffix}"
    try:
        async with _client() as client:
            admin_token = await _login_token(client, *ADMIN)
            created = await _create_user(client, admin_token, username)
            assert created.status_code == 200, created.text
            user_id = int(created.json()["data"]["id"])

            deleted = await client.delete(
                f"{API}/sys/users/{user_id}", headers=_auth(admin_token)
            )
            assert deleted.status_code == 200, deleted.text

            listing = await client.get(
                f"{API}/sys/users", headers=_auth(admin_token), params={"keyword": username}
            )
            assert listing.status_code == 200, listing.text
            assert listing.json()["data"]["items"] == []

            async with AsyncSessionLocal() as session:
                row = (
                    await session.execute(select(SysUser).where(SysUser.id == user_id))
                ).scalar_one_or_none()
                assert row is not None
                assert row.is_deleted is True
    finally:
        await _cleanup(username)
