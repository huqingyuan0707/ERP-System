"""B-06 员工与部门验收测试。

覆盖：部门树新增/编辑/排序与防环、员工编码唯一与按部门筛选、
删除有子部门/员工/用户的部门 409、员工关联用户后 sys_user.dept_id 与数据范围、
停用员工后新单据不可选、操作日志完整、按钮权限 403。
"""

# pyright: reportImplicitRelativeImport=false
# backend/ 才是导入根（cd backend && pytest），app 是顶层包；IDE 内的 basedpyright
# 未加载 backend/pyrightconfig.json 时会把 from app.xxx 误判为隐式相对导入。
# 仅在本文件关闭这条误报规则，保持绝对导入写法，避免改成相对导入后 pytest 启动失败。

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import httpx
from sqlalchemy import delete, select

from app.core.data_scope import resolve_scope
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.base_data import BdEmployee
from app.models.sys import SysDept, SysOperLog, SysRole, SysRolePermission, SysUser, SysUserRole


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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login_token(client: httpx.AsyncClient, username: str, password: str) -> str:
    resp = await client.post(f"{API}/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["data"]["accessToken"])


def _unique(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


async def _cleanup(dept_ids: list[int], employee_ids: list[int], user_ids: list[int]) -> None:
    """物理清理本测试产生的数据，先删引用方再删部门。"""
    async with AsyncSessionLocal() as session:
        await session.execute(delete(BdEmployee).where(BdEmployee.id.in_(employee_ids)))
        for user_id in user_ids:
            await session.execute(delete(SysUserRole).where(SysUserRole.user_id == user_id))
        await session.execute(delete(SysUser).where(SysUser.id.in_(user_ids)))
        await session.execute(delete(SysDept).where(SysDept.id.in_(dept_ids)))
        await session.commit()


async def _scenario() -> None:
    suffix = uuid.uuid4().hex[:8]
    name_a, name_b, name_c = (
        f"B06总部{suffix}",
        f"B06采购部{suffix}",
        f"B06独立部{suffix}",
    )
    code_a = _unique("EMP")
    username = f"b06user{suffix}"

    dept_ids: list[int] = []
    employee_ids: list[int] = []
    user_ids: list[int] = []

    async with _client() as client:
        token = await _login_token(client, *ADMIN)
        headers = _auth(token)
        try:
            # 1. 部门树新增、编辑、排序与防环
            resp = await client.post(
                f"{API}/base/depts",
                headers=headers,
                json={"name": name_a, "sort": 10, "status": 1},
            )
            assert resp.status_code == 200, resp.text
            dept_a = int(resp.json()["data"]["id"])
            dept_ids.append(dept_a)

            resp = await client.post(
                f"{API}/base/depts",
                headers=headers,
                json={"name": name_b, "parentId": dept_a, "sort": 20},
            )
            assert resp.status_code == 200, resp.text
            dept_b = int(resp.json()["data"]["id"])
            dept_ids.append(dept_b)

            resp = await client.get(f"{API}/base/depts", headers=headers)
            assert resp.status_code == 200, resp.text
            tree = resp.json()["data"]
            by_id = {int(item["id"]): item for item in tree}
            assert by_id[dept_b]["parentId"] == dept_a
            assert by_id[dept_a]["childrenCount"] == 1
            assert [item["id"] for item in tree if item["id"] in (dept_a, dept_b)] == [
                dept_a,
                dept_b,
            ]

            resp = await client.put(
                f"{API}/base/depts/{dept_b}",
                headers=headers,
                json={"sort": 5, "leader": "测试负责人"},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["leader"] == "测试负责人"
            resp = await client.get(f"{API}/base/depts", headers=headers)
            ids = [item["id"] for item in resp.json()["data"] if item["id"] in (dept_a, dept_b)]
            assert ids == [
                dept_b,
                dept_a,
            ]

            resp = await client.put(
                f"{API}/base/depts/sort",
                headers=headers,
                json={"items": [{"id": dept_a, "sort": 1}, {"id": dept_b, "sort": 2}]},
            )
            assert resp.status_code == 200, resp.text
            resp = await client.get(f"{API}/base/depts", headers=headers)
            ids = [item["id"] for item in resp.json()["data"] if item["id"] in (dept_a, dept_b)]
            assert ids == [
                dept_a,
                dept_b,
            ]

            # 上级不能是自己的下级；部门名称同租户唯一
            resp = await client.put(
                f"{API}/base/depts/{dept_a}", headers=headers, json={"parentId": dept_b}
            )
            assert resp.status_code == 422, resp.text
            resp = await client.post(f"{API}/base/depts", headers=headers, json={"name": name_a})
            assert resp.status_code == 409, resp.text

            # 2. 员工新增、编码唯一、按部门筛选
            resp = await client.post(
                f"{API}/base/employees",
                headers=headers,
                json={
                    "code": code_a,
                    "name": "员工甲",
                    "deptId": dept_b,
                    "position": "采购专员",
                    "entryDate": "2026-01-05",
                    "status": 1,
                },
            )
            assert resp.status_code == 200, resp.text
            emp_a = int(resp.json()["data"]["id"])
            employee_ids.append(emp_a)
            assert resp.json()["data"]["deptName"] == name_b
            assert resp.json()["data"]["entryDate"] == "2026-01-05"

            resp = await client.post(
                f"{API}/base/employees",
                headers=headers,
                json={"code": code_a, "name": "编码重复", "deptId": dept_a},
            )
            assert resp.status_code == 409, resp.text

            resp = await client.post(
                f"{API}/base/employees",
                headers=headers,
                json={"name": "员工乙", "deptId": dept_a},
            )
            assert resp.status_code == 200, resp.text
            emp_b = int(resp.json()["data"]["id"])
            employee_ids.append(emp_b)
            assert resp.json()["data"]["code"].startswith("EMP")

            resp = await client.get(
                f"{API}/base/employees",
                headers=headers,
                params={"deptId": dept_a, "includeSubDept": True, "pageSize": 100},
            )
            assert resp.status_code == 200, resp.text
            ids_with_children = {int(row["id"]) for row in resp.json()["data"]["items"]}
            assert emp_a in ids_with_children and emp_b in ids_with_children
            resp = await client.get(
                f"{API}/base/employees",
                headers=headers,
                params={"deptId": dept_a, "pageSize": 100},
            )
            ids_exact = {int(row["id"]) for row in resp.json()["data"]["items"]}
            assert emp_b in ids_exact and emp_a not in ids_exact

            # 3. 删除有子部门 / 员工 / 用户的部门返回 409
            resp = await client.delete(f"{API}/base/depts/{dept_a}", headers=headers)
            assert resp.status_code == 409, resp.text
            resp = await client.delete(f"{API}/base/depts/{dept_b}", headers=headers)
            assert resp.status_code == 409, resp.text

            resp = await client.post(f"{API}/base/depts", headers=headers, json={"name": name_c})
            assert resp.status_code == 200, resp.text
            dept_c = int(resp.json()["data"]["id"])
            dept_ids.append(dept_c)
            resp = await client.post(
                f"{API}/sys/users",
                headers=headers,
                json={
                    "username": username,
                    "password": PASSWORD,
                    "deptId": dept_c,
                    "roleIds": [],
                },
            )
            assert resp.status_code == 200, resp.text
            user_id = int(resp.json()["data"]["id"])
            user_ids.append(user_id)
            resp = await client.delete(f"{API}/base/depts/{dept_c}", headers=headers)
            assert resp.status_code == 409, resp.text

            # 4. 员工关联用户后，用户部门与数据范围同步
            resp = await client.put(
                f"{API}/base/employees/{emp_a}",
                headers=headers,
                json={"userId": user_id, "deptId": dept_a, "position": "采购主管"},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["username"] == username
            resp = await client.get(f"{API}/sys/users/{user_id}", headers=headers)
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["deptId"] == dept_a
            assert resp.json()["data"]["position"] == "采购主管"

            async with AsyncSessionLocal() as session:
                linked_user = await session.get(SysUser, user_id)
                assert linked_user is not None
                ctx = await resolve_scope(session, linked_user)
                assert ctx.dept_id == dept_a

            # 用户不再引用 C，部门可删除
            resp = await client.delete(f"{API}/base/depts/{dept_c}", headers=headers)
            assert resp.status_code == 200, resp.text

            # 同一账号不能被第二个员工重复关联
            resp = await client.post(
                f"{API}/base/employees",
                headers=headers,
                json={"name": "重复关联", "deptId": dept_a, "userId": user_id},
            )
            assert resp.status_code == 409, resp.text

            # 5. 停用员工后新单据不可选，历史档案仍可查
            resp = await client.put(
                f"{API}/base/employees/{emp_a}", headers=headers, json={"status": 0}
            )
            assert resp.status_code == 200, resp.text
            resp = await client.get(f"{API}/base/employees/options", headers=headers)
            assert resp.status_code == 200, resp.text
            assert emp_a not in {int(row["id"]) for row in resp.json()["data"]}
            assert emp_b in {int(row["id"]) for row in resp.json()["data"]}
            resp = await client.get(f"{API}/base/employees/{emp_a}", headers=headers)
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["status"] == 0

            # 6. 写操作日志完整（成功日志同一事务提交）
            async with AsyncSessionLocal() as session:
                actions = set(
                    (
                        await session.execute(
                            select(SysOperLog.action).where(
                                SysOperLog.module == "基础资料",
                                SysOperLog.status == 1,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            for expected in (
                "新增部门",
                "修改部门",
                "调整部门排序",
                "删除部门",
                "新增员工",
                "修改员工",
                "关联系统用户",
                "停用员工",
            ):
                assert expected in actions, f"缺少操作日志: {expected}"
        finally:
            await _cleanup(dept_ids, employee_ids, user_ids)


def test_dept_employee_b06_rules() -> None:
    _run(_scenario())


async def _permission_scenario() -> None:
    """角色缺少 base:dept:* / base:employee:* 时接口返回 403。"""
    suffix = uuid.uuid4().hex[:8]
    username = f"b06perm{suffix}"
    role_code = f"b06_role_{suffix}"
    role_id = 0
    user_id = 0
    async with AsyncSessionLocal() as session:
        role = SysRole(code=role_code, name="B06权限测试角色", data_scope=1, status=1, tenant_id=1)
        session.add(role)
        await session.flush()
        role_id = int(role.id)
        user = SysUser(
            username=username,
            password_hash=hash_password(PASSWORD),
            status=1,
            tenant_id=1,
        )
        session.add(user)
        await session.flush()
        user_id = int(user.id)
        session.add(SysUserRole(user_id=user.id, role_id=role.id))
        await session.commit()

    try:
        async with _client() as client:
            token = await _login_token(client, username, PASSWORD)
            headers = _auth(token)
            resp = await client.get(f"{API}/base/depts", headers=headers)
            assert resp.status_code == 403, resp.text
            resp = await client.get(f"{API}/base/employees", headers=headers)
            assert resp.status_code == 403, resp.text
            resp = await client.post(
                f"{API}/base/depts", headers=headers, json={"name": "无权新增"}
            )
            assert resp.status_code == 403, resp.text
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(SysUserRole).where(SysUserRole.user_id == user_id))
            await session.execute(delete(SysUser).where(SysUser.id == user_id))
            await session.execute(
                delete(SysRolePermission).where(SysRolePermission.role_id == role_id)
            )
            await session.execute(delete(SysRole).where(SysRole.id == role_id))
            await session.commit()


def test_dept_employee_permissions() -> None:
    _run(_permission_scenario())
