"""C-04 验收测试：数据范围解析（本人/本部门/全部、多角色最宽、admin 固定全部）、
范围过滤与越权判定、data-scope 分配接口。"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import httpx
from sqlalchemy import delete, select

from app.core.data_scope import allows, apply_scope, resolve_scope
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.base_data import BdGoods
from app.models.sys import (
    SysDept,
    SysRole,
    SysRoleDept,
    SysRolePermission,
    SysUser,
    SysUserRole,
)


API = "/api/v1"
ADMIN = ("admin", "admin123")
PASSWORD = "Passw0rd123"


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


@asynccontextmanager
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


async def _create_dept(name: str, parent_id: int | None) -> int:
    async with AsyncSessionLocal() as session:
        dept = SysDept(name=name, parent_id=parent_id, sort=99, status=1)
        session.add(dept)
        await session.commit()
        return int(dept.id)


async def _create_user(username: str, dept_id: int | None, role_ids: list[int]) -> int:
    async with AsyncSessionLocal() as session:
        user = SysUser(
            username=username,
            password_hash=hash_password(PASSWORD),
            real_name="数据范围测试",
            dept_id=dept_id,
            status=1,
        )
        session.add(user)
        await session.flush()
        for role_id in role_ids:
            session.add(SysUserRole(user_id=user.id, role_id=role_id))
        await session.commit()
        return int(user.id)


async def _create_role(code: str, data_scope: int) -> int:
    async with AsyncSessionLocal() as session:
        role = SysRole(code=code, name=f"范围角色{code}", data_scope=data_scope, status=1)
        session.add(role)
        await session.commit()
        return int(role.id)


async def _create_goods(name: str, created_by: int) -> int:
    async with AsyncSessionLocal() as session:
        goods = BdGoods(
            name=name,
            code="DS" + uuid.uuid4().hex[:8],
            cost_price=1,
            sale_price=2,
            status=1,
        )
        session.add(goods)
        await session.flush()
        goods.created_by = created_by
        await session.commit()
        return int(goods.id)


async def _cleanup(
    username: str, role_id: int, dept_ids: list[int], goods_names: list[str]
) -> None:
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(SysUser).where(SysUser.username == username))
        ).scalar_one_or_none()
        if user is not None:
            await session.execute(delete(SysUserRole).where(SysUserRole.user_id == user.id))
            await session.execute(delete(SysUser).where(SysUser.id == user.id))
        await session.execute(
            delete(SysRolePermission).where(SysRolePermission.role_id == role_id)
        )
        await session.execute(delete(SysRoleDept).where(SysRoleDept.role_id == role_id))
        await session.execute(delete(SysRole).where(SysRole.id == role_id))
        await session.execute(delete(BdGoods).where(BdGoods.name.in_(goods_names)))
        for dept_id in dept_ids:
            await session.execute(delete(SysDept).where(SysDept.id == dept_id))
        await session.commit()


def test_scope_widest_and_admin() -> None:
    """多角色取最宽范围；admin 固定全部。"""
    suffix = _suffix()

    async def _assert() -> None:
        async with AsyncSessionLocal() as session:
            admin = (
                await session.execute(select(SysUser).where(SysUser.username == ADMIN[0]))
            ).scalar_one()
            ctx = await resolve_scope(session, admin)
            assert ctx.scope == 3

    _run(_assert())

    role_self = _run(_create_role(f"rs{suffix}", 1))
    role_dept = _run(_create_role(f"rd{suffix}", 2))
    username = f"u{suffix}"

    async def _assert_widest() -> None:
        user_id = await _create_user(username, None, [role_self, role_dept])
        async with AsyncSessionLocal() as session:
            user = await session.get(SysUser, user_id)
            assert user is not None
            ctx = await resolve_scope(session, user)
            assert ctx.scope == 2

    _run(_assert_widest())
    _run(_cleanup(username, role_self, [], []))
    _run(_cleanup(username, role_dept, [], []))


def test_apply_scope_self_dept_all() -> None:
    """同一批数据：本人范围看 1 条、本部门子树看本部门条数、全部看全量。"""
    suffix = _suffix()
    dept_root = _run(_create_dept(f"部A{suffix}", None))
    dept_child = _run(_create_dept(f"部A1{suffix}", dept_root))
    dept_other = _run(_create_dept(f"部B{suffix}", None))

    u_a = f"ua{suffix}"
    u_a1 = f"ua1{suffix}"
    u_b = f"ub{suffix}"
    role_self = _run(_create_role(f"rs{suffix}", 1))
    role_dept = _run(_create_role(f"rd{suffix}", 2))
    role_all = _run(_create_role(f"ra{suffix}", 3))

    user_a = _run(_create_user(u_a, dept_root, [role_dept]))
    user_a1 = _run(_create_user(u_a1, dept_child, [role_dept]))
    user_b = _run(_create_user(u_b, dept_other, [role_all]))

    goods_names = [f"商品{suffix}-{i}" for i in range(1, 5)]
    goods_self = _run(_create_goods(goods_names[0], user_a))
    goods_child = _run(_create_goods(goods_names[1], user_a1))
    goods_other = _run(_create_goods(goods_names[2], user_b))
    goods_admin = _run(_create_goods(goods_names[3], 1))

    try:

        async def _assert() -> None:
            async with AsyncSessionLocal() as session:
                from app.models.sys import SysUser as _SysUser

                user_self = (
                    await session.execute(select(_SysUser).where(_SysUser.id == user_a))
                ).scalar_one()
                ctx_all = await resolve_scope(session, user_self)
                ctx_all.scope = 1
                user_dept_ctx = await resolve_scope(session, user_self)
                user_admin_obj = (
                    await session.execute(
                        select(_SysUser).where(_SysUser.username == ADMIN[0])
                    )
                ).scalar_one()
                ctx_admin = await resolve_scope(session, user_admin_obj)

                # 本人：仅 user_a 创建的
                stmt = await apply_scope(session, select(BdGoods.name), BdGoods, ctx_all)
                names = set((await session.execute(stmt)).scalars().all())
                assert names == {goods_names[0]}

                # 本部门：A 部门子树（A + A1）创建的，不含 B 部门与 admin（默认部门为空 → 不在子树）
                stmt = await apply_scope(session, select(BdGoods.name), BdGoods, user_dept_ctx)
                names = set((await session.execute(stmt)).scalars().all())
                assert names == {goods_names[0], goods_names[1]}

                # 全部：包含 admin 创建与 B 部门创建
                stmt = await apply_scope(session, select(BdGoods.name), BdGoods, ctx_admin)
                names = set((await session.execute(stmt)).scalars().all())
                assert names >= {
                    goods_names[0],
                    goods_names[1],
                    goods_names[2],
                    goods_names[3],
                }

                # 越权判定：本人范围访问他人单据 → False；B 部门用户访问 A 部门单据 → False
                ctx_b = await resolve_scope(
                    session,
                    (
                        await session.execute(
                            select(_SysUser).where(_SysUser.id == user_b)
                        )
                    ).scalar_one(),
                )
                ctx_b.scope = 1
                assert not await allows(session, ctx_b, created_by=user_a)
                assert await allows(session, ctx_b, created_by=user_b)
                # 本部门范围：子树内部门单据可见，子树外不可见
                assert await allows(session, user_dept_ctx, created_by=user_a1)
                assert not await allows(session, user_dept_ctx, created_by=user_b)

        _run(_assert())
    finally:
        _run(_cleanup(u_a, role_dept, [dept_root, dept_child, dept_other], goods_names))
        _run(_cleanup(u_a1, role_dept, [], []))
        _run(_cleanup(u_b, role_all, [], []))
        _run(_cleanup(u_a, role_self, [], []))
        assert goods_self and goods_child and goods_other and goods_admin


def test_data_scope_assign_api() -> None:
    """data-scope 分配接口：保存、非法范围 422、无效部门 422、admin 角色不可删。"""
    suffix = _suffix()
    token = _admin_login()
    dept_id = _run(_create_dept(f"部C{suffix}", None))
    role_id = _run(_create_role(f"rc{suffix}", 1))

    async def _assert() -> None:
        async with _client() as client:
            resp = await client.get(f"{API}/sys/roles", headers=_auth(token))
            assert resp.status_code == 200, resp.text
            roles = {r["code"]: r for r in resp.json()["data"]}
            assert "admin" in roles

            resp = await client.put(
                f"{API}/sys/roles/{role_id}/data-scope",
                json={"dataScope": 2, "deptIds": [dept_id]},
                headers=_auth(token),
            )
            assert resp.status_code == 200, resp.text
            resp = await client.get(f"{API}/sys/roles", headers=_auth(token))
            target = next(r for r in resp.json()["data"] if r["id"] == role_id)
            assert target["dataScope"] == 2
            assert target["deptIds"] == [dept_id]

            resp = await client.put(
                f"{API}/sys/roles/{role_id}/data-scope",
                json={"dataScope": 4},
                headers=_auth(token),
            )
            assert resp.status_code == 422

            resp = await client.put(
                f"{API}/sys/roles/{role_id}/data-scope",
                json={"dataScope": 2, "deptIds": [99999999]},
                headers=_auth(token),
            )
            assert resp.status_code == 422

            resp = await client.get(f"{API}/sys/roles", headers=_auth(token))
            roles = resp.json()["data"]
            admin_role_id = next(r["id"] for r in roles if r["code"] == "admin")
            resp = await client.delete(
                f"{API}/sys/roles/{admin_role_id}", headers=_auth(token)
            )
            assert resp.status_code == 400

    _run(_assert())
    _run(_cleanup("", role_id, [dept_id], []))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _admin_login() -> str:
    async def _login() -> str:
        async with _client() as client:
            resp = await client.post(
                f"{API}/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]}
            )
            assert resp.status_code == 200, resp.text
            return str(resp.json()["data"]["accessToken"])

    return asyncio.run(_login())
