"""C-03 菜单/按钮权限验收测试：动态菜单、写接口鉴权、角色权限分配、admin 全量、禁用登录。"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import httpx
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.sys import (
    SysPermission,
    SysRole,
    SysRolePermission,
    SysUser,
    SysUserRole,
)


API = "/api/v1"
AUTH = f"{API}/auth"
ADMIN = ("admin", "admin123")
PASSWORD = "Passw0rd123"


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


@asynccontextmanager
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _perm_ids_by_codes(codes: list[str]) -> list[int]:
    """按权限码查询权限 id。"""
    async with AsyncSessionLocal() as session:
        stmt = select(SysPermission.id).where(SysPermission.perm_code.in_(codes))
        return list((await session.execute(stmt)).scalars().all())


async def _perm_id_by_route(route_path: str) -> int | None:
    """按路由路径查询目录节点 id（目录无权限码）。"""
    async with AsyncSessionLocal() as session:
        stmt = select(SysPermission.id).where(
            SysPermission.type == 1, SysPermission.route_path == route_path
        )
        return (await session.execute(stmt)).scalar_one_or_none()


async def _grant(role_id: int, perm_ids: list[int]) -> None:
    """为角色追加权限关联。"""
    async with AsyncSessionLocal() as session:
        for perm_id in perm_ids:
            session.add(SysRolePermission(role_id=role_id, permission_id=perm_id))
        await session.commit()


async def _create_role_with_perms(codes: list[str]) -> int:
    """创建角色并授予指定权限码，返回角色 id。"""
    async with AsyncSessionLocal() as session:
        role = SysRole(code=_role_code(), name="测试角色", data_scope=1, status=1)
        session.add(role)
        await session.flush()
        perm_ids = (
            (await session.execute(
                select(SysPermission.id).where(SysPermission.perm_code.in_(codes))
            ))
            .scalars()
            .all()
        )
        for perm_id in perm_ids:
            session.add(SysRolePermission(role_id=role.id, permission_id=int(perm_id)))
        await session.commit()
        return int(role.id)


async def _create_user_with_role(username: str, role_id: int, status: int = 1) -> int:
    """创建用户并绑定角色，返回用户 id。"""
    async with AsyncSessionLocal() as session:
        user = SysUser(
            username=username,
            password_hash=hash_password(PASSWORD),
            real_name="权限测试用户",
            status=status,
        )
        session.add(user)
        await session.flush()
        session.add(SysUserRole(user_id=user.id, role_id=role_id))
        await session.commit()
        return int(user.id)


async def _cleanup(username: str, role_id: int) -> None:
    """清理测试用户、角色及关联数据。"""
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
        await session.execute(delete(SysRole).where(SysRole.id == role_id))
        await session.commit()


async def _login(client: httpx.AsyncClient, username: str, password: str) -> httpx.Response:
    return await client.post(f"{AUTH}/login", json={"username": username, "password": password})


async def _login_token(client: httpx.AsyncClient, username: str, password: str) -> str:
    resp = await _login(client, username, password)
    assert resp.status_code == 200, resp.text
    return str(resp.json()["data"]["accessToken"])


async def _get_me(client: httpx.AsyncClient, token: str) -> dict[str, Any]:
    resp = await client.get(f"{AUTH}/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    return dict(resp.json()["data"])


def _menu_names(menus: list[dict[str, Any]]) -> list[str]:
    return [menu["name"] for menu in menus]


def _role_code() -> str:
    return "t" + uuid.uuid4().hex[:10]


def _new_username() -> str:
    return "t" + uuid.uuid4().hex[:12]


def _admin_login() -> str:
    async def _login() -> str:
        async with _client() as client:
            return await _login_token(client, *ADMIN)

    return asyncio.run(_login())


def test_purchase_only_role_menus() -> None:
    """验收①：只授采购菜单的角色，菜单树仅剩采购管理。"""
    username = _new_username()
    codes = ["pur:apply:list", "pur:order:list", "pur:inbound:list", "pur:return:list"]
    role_id = _run(_create_role_with_perms(codes))

    async def _grant_purchase_dir() -> None:
        dir_id = await _perm_id_by_route("/purchase")
        assert dir_id is not None
        await _grant(role_id, [dir_id])

    _run(_grant_purchase_dir())
    try:
        _run(_create_user_with_role(username, role_id))

        async def _assert_menus() -> None:
            async with _client() as client:
                token = await _login_token(client, username, PASSWORD)
                me = await _get_me(client, token)
            assert _menu_names(me["menus"]) == ["采购管理"]
            purchase = me["menus"][0]
            assert purchase["routePath"] == "/purchase"
            assert _menu_names(purchase["children"]) == [
                "采购申请",
                "采购订单",
                "采购入库",
                "采购退货",
            ]
            assert "*" not in me["permissions"]
            assert "base:goods:list" not in me["permissions"]

        _run(_assert_menus())
    finally:
        _run(_cleanup(username, role_id))


def test_missing_perm_forbidden() -> None:
    """验收②：无权限的用户访问列表与创建接口均返回 403。"""
    username = _new_username()
    role_id = _run(_create_role_with_perms(["pur:order:list"]))
    try:
        _run(_create_user_with_role(username, role_id))

        async def _assert_forbidden() -> None:
            async with _client() as client:
                token = await _login_token(client, username, PASSWORD)
                headers = {"Authorization": f"Bearer {token}"}
                resp = await client.get(f"{API}/base/goods", headers=headers)
                assert resp.status_code == 403
                resp = await client.post(f"{API}/base/goods", headers=headers, json={})
                assert resp.status_code == 403
                resp = await client.get(f"{API}/sys/users", headers=headers)
                assert resp.status_code == 403
                resp = await client.get(f"{API}/sys/permissions/tree", headers=headers)
                assert resp.status_code == 403

        _run(_assert_forbidden())
    finally:
        _run(_cleanup(username, role_id))


def test_role_permission_reassign_syncs() -> None:
    """验收③：角色权限调整后重新登录，菜单与按钮权限同步变化。"""
    username = _new_username()
    role_id = _run(_create_role_with_perms(["pur:order:list"]))
    try:
        _run(_create_user_with_role(username, role_id))
        admin_token = _admin_login()

        async def _assert_and_reassign() -> None:
            async with _client() as client:
                # 初始：采购订单菜单未挂目录，作为根节点展示
                token = await _login_token(client, username, PASSWORD)
                me = await _get_me(client, token)
                assert _menu_names(me["menus"]) == ["采购订单"]

                # admin 调整角色权限为：基础资料目录 + 商品菜单 + 新增按钮
                base_dir_id = await _perm_id_by_route("/base")
                assert base_dir_id is not None
                perm_ids = await _perm_ids_by_codes(["base:goods:list", "base:goods:add"])
                resp = await client.put(
                    f"{API}/sys/roles/{role_id}/permissions",
                    json={"permissionIds": [base_dir_id, *perm_ids]},
                    headers={"Authorization": f"Bearer {admin_token}"},
                )
                assert resp.status_code == 200, resp.text

                # 重新登录后菜单与按钮权限同步
                token = await _login_token(client, username, PASSWORD)
                me = await _get_me(client, token)
                assert _menu_names(me["menus"]) == ["基础资料"]
                assert _menu_names(me["menus"][0]["children"]) == ["商品管理"]
                assert "base:goods:add" in me["permissions"]
                assert "pur:order:list" not in me["permissions"]

        _run(_assert_and_reassign())
    finally:
        _run(_cleanup(username, role_id))


def test_role_permission_assign_validation() -> None:
    """角色权限分配：无效权限 id 422、角色不存在 404。"""
    role_id = _run(_create_role_with_perms([]))
    try:
        admin_token = _admin_login()

        async def _assert_validation() -> None:
            async with _client() as client:
                headers = {"Authorization": f"Bearer {admin_token}"}
                resp = await client.put(
                    f"{API}/sys/roles/{role_id}/permissions",
                    json={"permissionIds": [99999999]},
                    headers=headers,
                )
                assert resp.status_code == 422
                resp = await client.put(
                    f"{API}/sys/roles/99999999/permissions",
                    json={"permissionIds": []},
                    headers=headers,
                )
                assert resp.status_code == 404

        _run(_assert_validation())
    finally:
        _run(_cleanup("", role_id))


def test_permission_tree() -> None:
    """权限树接口：admin 可查且包含三级节点；结构完整。"""
    admin_token = _admin_login()

    async def _assert_tree() -> None:
        async with _client() as client:
            resp = await client.get(
                f"{API}/sys/permissions/tree",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert resp.status_code == 200, resp.text
            tree = resp.json()["data"]

            def _walk(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
                for node in nodes:
                    yield node
                    yield from _walk(node["children"])

            flat = list(_walk(tree))
            assert {node["type"] for node in flat} == {1, 2, 3}
            codes = {node["permCode"] for node in flat}
            assert "sys:user:add" in codes
            assert "rpt:dashboard:list" in codes
            assert "sys:role:assign" in codes

    _run(_assert_tree())


def test_admin_full_menus() -> None:
    """验收④：admin 角色可见全部菜单（含经营看板与全部目录）。"""
    admin_token = _admin_login()

    async def _assert_admin() -> None:
        async with _client() as client:
            me = await _get_me(client, admin_token)
            assert "*" in me["permissions"]
            names = _menu_names(me["menus"])
            assert "经营看板" in names
            for dir_name in (
                "系统管理",
                "基础资料",
                "采购管理",
                "销售管理",
                "库存管理",
                "财务管理",
                "报表中心",
            ):
                assert dir_name in names

    _run(_assert_admin())


def test_disabled_user_cannot_login() -> None:
    """禁用用户无法登录。"""
    username = _new_username()
    role_id = _run(_create_role_with_perms([]))
    try:
        _run(_create_user_with_role(username, role_id, status=0))

        async def _assert_login_forbidden() -> None:
            async with _client() as client:
                resp = await _login(client, username, PASSWORD)
                assert resp.status_code == 403
                assert "禁用" in resp.json()["message"]

        _run(_assert_login_forbidden())
    finally:
        _run(_cleanup(username, role_id))
