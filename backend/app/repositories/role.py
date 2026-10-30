"""角色数据访问。"""

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sys import SysRole, SysRoleDept, SysRolePermission, SysUserRole


class RoleRepository:
    """角色数据访问（不含业务规则）。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_all(self) -> list[SysRole]:
        """全部未删除角色，按 id 排序。"""
        stmt = select(SysRole).where(SysRole.is_deleted.is_(False)).order_by(SysRole.id)
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_by_id(self, role_id: int) -> SysRole | None:
        """按 id 查询角色（未删除）。"""
        return await self._db.scalar(
            select(SysRole).where(SysRole.id == role_id, SysRole.is_deleted.is_(False))
        )

    async def get_by_code(self, code: str) -> SysRole | None:
        """按 code 查询角色（未删除）。"""
        return await self._db.scalar(
            select(SysRole).where(SysRole.code == code, SysRole.is_deleted.is_(False))
        )

    async def count_user_roles(self, role_id: int) -> int:
        """统计角色绑定的用户数。"""
        stmt = (
            select(func.count()).select_from(SysUserRole).where(SysUserRole.role_id == role_id)
        )
        return int((await self._db.execute(stmt)).scalar_one())

    async def list_dept_ids(self, role_id: int) -> list[int]:
        """角色已配置的数据权限部门 id。"""
        stmt = select(SysRoleDept.dept_id).where(SysRoleDept.role_id == role_id)
        return list((await self._db.execute(stmt)).scalars().all())

    async def replace_dept_ids(self, role_id: int, dept_ids: list[int]) -> None:
        """重建角色-部门关联（全删全插）。"""
        await self._db.execute(delete(SysRoleDept).where(SysRoleDept.role_id == role_id))
        for dept_id in dept_ids:
            self._db.add(SysRoleDept(role_id=role_id, dept_id=dept_id))

    async def count_permissions(self, role_id: int) -> int:
        """统计角色已分配权限数（删除前检查）。"""
        stmt = (
            select(func.count())
            .select_from(SysRolePermission)
            .where(SysRolePermission.role_id == role_id)
        )
        return int((await self._db.execute(stmt)).scalar_one())
