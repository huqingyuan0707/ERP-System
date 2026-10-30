"""权限数据访问。"""

from sqlalchemy import delete, select

from app.models.sys import SysPermission, SysRolePermission
from app.repositories.base import BaseRepository


class PermissionRepository(BaseRepository[SysPermission]):
    """权限仓储。"""

    model = SysPermission

    async def list_all(self) -> list[SysPermission]:
        """查询全部未删除权限，按 sort、id 排序。"""
        stmt = (
            select(SysPermission)
            .where(SysPermission.is_deleted.is_(False))
            .order_by(SysPermission.sort, SysPermission.id)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_existing_ids(self, permission_ids: list[int]) -> set[int]:
        """查询给定 id 中真实存在且未删除的权限 id 集合。"""
        stmt = select(SysPermission.id).where(
            SysPermission.id.in_(permission_ids), SysPermission.is_deleted.is_(False)
        )
        return set((await self.db.execute(stmt)).scalars().all())

    async def replace_role_permissions(self, role_id: int, permission_ids: list[int]) -> None:
        """重建角色的权限关联。"""
        await self.db.execute(delete(SysRolePermission).where(SysRolePermission.role_id == role_id))
        for permission_id in dict.fromkeys(permission_ids):
            self.db.add(SysRolePermission(role_id=role_id, permission_id=permission_id))
        await self.db.flush()
