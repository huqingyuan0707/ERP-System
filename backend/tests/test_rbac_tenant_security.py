"""RBAC 多租户边界回归；全部使用测试夹具创建的临时 SQLite。"""

import asyncio
import uuid
from collections.abc import Coroutine
from typing import Any

import pytest
from sqlalchemy import delete, func, select

from app.core.data_scope import SCOPE_SELF, resolve_scope
from app.core.exceptions import AppException
from app.core.security import hash_password
from app.db.init_db import DEFAULT_ADMIN_PASSWORD, _ensure_permissions, _seed_admin
from app.db.session import AsyncSessionLocal
from app.models.sys import (
    SysDept,
    SysPermission,
    SysRole,
    SysRoleDept,
    SysRolePermission,
    SysUser,
    SysUserRole,
)
from app.repositories.permission import PermissionRepository
from app.repositories.role import RoleRepository
from app.repositories.user import UserRepository
from app.schemas.role import DataScopeAssign, RoleCreate
from app.schemas.user import UserCreate, UserUpdate
from app.services.auth import AuthService
from app.services.permission import PermissionService
from app.services.role import RoleService
from app.services.user import UserService


PASSWORD = "Passw0rd123"


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


def _suffix() -> str:
    return uuid.uuid4().hex[:10]


async def _cleanup(ids: dict[str, list[int]]) -> None:
    async with AsyncSessionLocal() as session:
        user_ids = ids.get("users", [])
        role_ids = ids.get("roles", [])
        dept_ids = ids.get("depts", [])
        if user_ids:
            await session.execute(delete(SysUserRole).where(SysUserRole.user_id.in_(user_ids)))
            await session.execute(delete(SysUser).where(SysUser.id.in_(user_ids)))
        if role_ids:
            await session.execute(
                delete(SysRolePermission).where(SysRolePermission.role_id.in_(role_ids))
            )
            await session.execute(delete(SysRoleDept).where(SysRoleDept.role_id.in_(role_ids)))
            await session.execute(delete(SysRole).where(SysRole.id.in_(role_ids)))
        if dept_ids:
            await session.execute(delete(SysDept).where(SysDept.id.in_(dept_ids)))
        await session.commit()


def test_cross_tenant_role_department_and_permission_writes_are_rejected() -> None:
    ids: dict[str, list[int]] = {"users": [], "roles": [], "depts": []}

    async def scenario() -> None:
        suffix = _suffix()
        async with AsyncSessionLocal() as session:
            foreign_dept = SysDept(tenant_id=2, name=f"foreign-dept-{suffix}", status=1)
            foreign_role = SysRole(
                tenant_id=2,
                code=f"foreign_role_{suffix}",
                name="外租户角色",
                status=1,
            )
            session.add_all([foreign_dept, foreign_role])
            await session.flush()
            ids["depts"].append(foreign_dept.id)
            ids["roles"].append(foreign_role.id)
            await session.commit()

            role_service = RoleService(session, tenant_id=1)
            with pytest.raises(AppException) as exc:
                await role_service.assign_data_scope(
                    foreign_role.id,
                    DataScopeAssign(dataScope=2, deptIds=[foreign_dept.id]),
                )
            assert exc.value.code == 404

            permission_id = await session.scalar(
                select(SysPermission.id).where(SysPermission.perm_code == "base:goods:list")
            )
            assert permission_id is not None
            with pytest.raises(AppException) as exc:
                await PermissionService(session, tenant_id=1).assign_role_permissions(
                    foreign_role.id, [permission_id]
                )
            assert exc.value.code == 404

            with pytest.raises(ValueError):
                await RoleRepository(session, tenant_id=1).replace_dept_ids(
                    foreign_role.id, [foreign_dept.id]
                )
            with pytest.raises(ValueError):
                await PermissionRepository(session, tenant_id=1).replace_role_permissions(
                    foreign_role.id, [permission_id]
                )

    try:
        _run(scenario())
    finally:
        _run(_cleanup(ids))


def test_user_create_update_refs_and_created_rows_are_tenant_scoped() -> None:
    ids: dict[str, list[int]] = {"users": [], "roles": [], "depts": []}

    async def scenario() -> None:
        suffix = _suffix()
        async with AsyncSessionLocal() as session:
            local_dept = SysDept(tenant_id=1, name=f"local-{suffix}", status=1)
            foreign_dept = SysDept(tenant_id=2, name=f"foreign-{suffix}", status=1)
            local_role = SysRole(tenant_id=1, code=f"local_{suffix}", name="本租户角色", status=1)
            foreign_role = SysRole(
                tenant_id=2, code=f"foreign_{suffix}", name="外租户角色", status=1
            )
            session.add_all([local_dept, foreign_dept, local_role, foreign_role])
            await session.flush()
            local_dept_id = local_dept.id
            foreign_dept_id = foreign_dept.id
            local_role_id = local_role.id
            foreign_role_id = foreign_role.id
            ids["depts"].extend([local_dept_id, foreign_dept_id])
            ids["roles"].extend([local_role_id, foreign_role_id])
            await session.commit()

            service = UserService(session, tenant_id=1)
            with pytest.raises(AppException) as exc:
                await service.create_user(
                    UserCreate(
                        username=f"bad-dept-{suffix}",
                        password=PASSWORD,
                        deptId=foreign_dept_id,
                        roleIds=[],
                    ),
                    current_user_id=1,
                )
            assert exc.value.code == 422
            await session.rollback()

            with pytest.raises(AppException) as exc:
                await service.create_user(
                    UserCreate(
                        username=f"bad-role-{suffix}",
                        password=PASSWORD,
                        deptId=local_dept_id,
                        roleIds=[foreign_role_id],
                    ),
                    current_user_id=1,
                )
            assert exc.value.code == 422
            await session.rollback()

            created = await service.create_user(
                UserCreate(
                    username=f"ok-{suffix}",
                    password=PASSWORD,
                    deptId=local_dept_id,
                    roleIds=[local_role_id],
                ),
                current_user_id=1,
            )
            ids["users"].append(created.id)
            row = await session.get(SysUser, created.id)
            assert row is not None and row.tenant_id == 1

            with pytest.raises(AppException) as exc:
                await service.update_user(
                    created.id,
                    UserUpdate(roleIds=[foreign_role_id]),
                    current_user_id=1,
                )
            assert exc.value.code == 422
            await session.rollback()
            with pytest.raises(AppException) as exc:
                await service.update_user(
                    created.id,
                    UserUpdate(deptId=foreign_dept_id),
                    current_user_id=1,
                )
            assert exc.value.code == 422

            with pytest.raises(ValueError):
                await UserRepository(session, tenant_id=1).replace_roles(
                    created.id, [foreign_role_id]
                )

            role = await RoleService(session, tenant_id=7).create_role(
                RoleCreate(code=f"created_{suffix}", name="显式租户角色")
            )
            ids["roles"].append(role.id)
            assert role.tenant_id == 7

    try:
        _run(scenario())
    finally:
        _run(_cleanup(ids))


def test_foreign_and_disabled_roles_do_not_grant_auth_or_data_scope() -> None:
    ids: dict[str, list[int]] = {"users": [], "roles": [], "depts": []}

    async def scenario() -> None:
        suffix = _suffix()
        async with AsyncSessionLocal() as session:
            permission = await session.scalar(
                select(SysPermission).where(SysPermission.perm_code == "base:goods:list")
            )
            assert permission is not None
            user = SysUser(
                tenant_id=1,
                username=f"tenant-user-{suffix}",
                password_hash=hash_password(PASSWORD),
                status=1,
            )
            disabled = SysRole(
                tenant_id=1,
                code=f"disabled_{suffix}",
                name="禁用角色",
                status=0,
                data_scope=3,
            )
            foreign = SysRole(
                tenant_id=2,
                code=f"foreign_{suffix}",
                name="外租户角色",
                status=1,
                data_scope=3,
            )
            session.add_all([user, disabled, foreign])
            await session.flush()
            ids["users"].append(user.id)
            ids["roles"].extend([disabled.id, foreign.id])
            for role in (disabled, foreign):
                session.add(SysUserRole(user_id=user.id, role_id=role.id))
                session.add(SysRolePermission(role_id=role.id, permission_id=permission.id))
            await session.commit()

            assert await AuthService(session).get_permission_codes(user.id) == []
            me = await AuthService(session).get_me(user)
            assert me.roles == [] and me.permissions == [] and me.menus == []
            scope = await resolve_scope(session, user)
            assert scope.scope == SCOPE_SELF

    try:
        _run(scenario())
    finally:
        _run(_cleanup(ids))


def test_ensure_export_permissions_is_global_idempotent_and_grants_each_admin() -> None:
    ids: dict[str, list[int]] = {"users": [], "roles": [], "depts": []}

    async def scenario() -> None:
        suffix = _suffix()
        async with AsyncSessionLocal() as session:
            second_admin = SysRole(
                tenant_id=2,
                code="admin",
                name=f"第二租户管理员-{suffix}",
                status=1,
                data_scope=3,
            )
            session.add(second_admin)
            await session.commit()
            ids["roles"].append(second_admin.id)

            await _ensure_permissions(session)
            await _ensure_permissions(session)
            await session.commit()

            codes = ("inv:stock:export", "report:export", "fin:statement:export")
            for code in codes:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(SysPermission)
                        .where(
                            SysPermission.perm_code == code,
                            SysPermission.is_deleted.is_(False),
                        )
                    )
                    == 1
                )
            admin_ids = list(
                (
                    await session.execute(
                        select(SysRole.id).where(
                            SysRole.code == "admin", SysRole.is_deleted.is_(False)
                        )
                    )
                )
                .scalars()
                .all()
            )
            granted = await session.scalar(
                select(func.count())
                .select_from(SysRolePermission)
                .join(
                    SysPermission,
                    SysPermission.id == SysRolePermission.permission_id,
                )
                .where(
                    SysRolePermission.role_id.in_(admin_ids),
                    SysPermission.perm_code.in_(codes),
                )
            )
            assert granted == len(admin_ids) * len(codes)

    try:
        _run(scenario())
    finally:
        _run(_cleanup(ids))


def test_seed_admin_uses_configured_password_or_development_default(monkeypatch) -> None:
    from app.core.security import verify_password
    from app.db import init_db

    async def seed_with(password: str | None) -> None:
        suffix = _suffix()
        async with AsyncSessionLocal() as session:
            original_admin = await session.scalar(
                select(SysUser).where(SysUser.username == "admin", SysUser.tenant_id == 1)
            )
            assert original_admin is not None
            original_admin_id = original_admin.id
            original_admin.username = f"seed-original-{suffix}"
            dept = SysDept(tenant_id=1, name=f"seed-dept-{suffix}", status=1)
            role = SysRole(
                tenant_id=1,
                code=f"seed_admin_{suffix}",
                name="种子管理员角色",
                status=1,
            )
            session.add_all([dept, role])
            await session.flush()
            ids = {"users": [], "roles": [role.id], "depts": [dept.id]}
            monkeypatch.setattr(init_db.settings, "INITIAL_ADMIN_PASSWORD", password)
            await _seed_admin(session, dept.id, role.id)
            admin = (
                (
                    await session.execute(
                        select(SysUser)
                        .where(SysUser.username == "admin")
                        .order_by(SysUser.id.desc())
                    )
                )
                .scalars()
                .first()
            )
            assert admin is not None
            ids["users"].append(admin.id)
            assert verify_password(password or DEFAULT_ADMIN_PASSWORD, admin.password_hash)
            await session.rollback()
            restored_username = await session.scalar(
                select(SysUser.username).where(SysUser.id == original_admin_id)
            )
            assert restored_username == "admin"

    _run(seed_with(None))
    _run(seed_with("Configured123"))
