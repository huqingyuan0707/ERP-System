"""角色数据访问。"""

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sys import SysDept, SysRole, SysRoleDept, SysRolePermission, SysUserRole


class RoleRepository:
    """角色数据访问（不含业务规则）。"""

    def __init__(self, db: AsyncSession, tenant_id: int | None = None) -> None:
        self._db = db
        self.tenant_id = tenant_id

    async def list_all(self) -> list[SysRole]:
        """全部未删除角色，按 id 排序。"""
        stmt = (
            select(SysRole)
            .where(
                SysRole.is_deleted.is_(False),
                SysRole.tenant_id == self.tenant_id,  # 租户隔离：只查当前租户角色
            )
            .order_by(SysRole.id)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_by_id(self, role_id: int) -> SysRole | None:
        """按 id 查询角色（未删除）。"""
        return await self._db.scalar(
            select(SysRole).where(
                SysRole.id == role_id,
                SysRole.is_deleted.is_(False),
                SysRole.tenant_id == self.tenant_id,  # 租户隔离
            )
        )

    async def get_by_code(self, code: str) -> SysRole | None:
        """按 code 查询角色（未删除）。"""
        return await self._db.scalar(
            select(SysRole).where(
                SysRole.code == code,
                SysRole.is_deleted.is_(False),
                SysRole.tenant_id == self.tenant_id,  # 租户隔离
            )
        )

    async def count_user_roles(self, role_id: int) -> int:
        """统计当前租户角色绑定的当前租户用户数。"""
        stmt = (
            select(func.count())
            .select_from(SysUserRole)
            .join(SysRole, SysRole.id == SysUserRole.role_id)
            .where(
                SysUserRole.role_id == role_id,
                SysRole.tenant_id == self.tenant_id,
                SysRole.is_deleted.is_(False),
            )
        )
        return int((await self._db.execute(stmt)).scalar_one())

    async def list_dept_ids(self, role_id: int) -> list[int]:
        """当前租户角色已配置且仍有效的数据权限部门 id。"""
        stmt = (
            select(SysRoleDept.dept_id)
            .join(SysRole, SysRole.id == SysRoleDept.role_id)
            .join(SysDept, SysDept.id == SysRoleDept.dept_id)
            .where(
                SysRoleDept.role_id == role_id,
                SysRole.tenant_id == self.tenant_id,
                SysRole.is_deleted.is_(False),
                SysDept.tenant_id == self.tenant_id,
                SysDept.is_deleted.is_(False),
            )
            .order_by(SysRoleDept.dept_id)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def replace_dept_ids(self, role_id: int, dept_ids: list[int]) -> None:
        """重建当前租户角色的部门关联。"""
        role = await self.get_by_id(role_id)
        if role is None:
            raise ValueError("role does not belong to current tenant")
        if dept_ids:
            valid_dept_ids = set(
                (
                    await self._db.execute(
                        select(SysDept.id).where(
                            SysDept.id.in_(dept_ids),
                            SysDept.tenant_id == self.tenant_id,
                            SysDept.is_deleted.is_(False),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if valid_dept_ids != set(dept_ids):
                raise ValueError("department does not belong to current tenant")
        await self._db.execute(delete(SysRoleDept).where(SysRoleDept.role_id == role_id))
        for dept_id in dict.fromkeys(dept_ids):
            self._db.add(SysRoleDept(role_id=role_id, dept_id=dept_id))

    async def count_permissions(self, role_id: int) -> int:
        """统计当前租户角色已分配权限数（删除前检查）。"""
        stmt = (
            select(func.count())
            .select_from(SysRolePermission)
            .join(SysRole, SysRole.id == SysRolePermission.role_id)
            .where(
                SysRolePermission.role_id == role_id,
                SysRole.tenant_id == self.tenant_id,
                SysRole.is_deleted.is_(False),
            )
        )
        return int((await self._db.execute(stmt)).scalar_one())
