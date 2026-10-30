"""用户数据访问。"""

from sqlalchemy import delete, or_, select

from app.models.sys import (
    SysPermission,
    SysRole,
    SysRolePermission,
    SysUser,
    SysUserRole,
)
from app.repositories.base import BaseRepository
from app.schemas.user import UserQuery


class UserRepository(BaseRepository[SysUser]):
    """用户仓储。"""

    model = SysUser

    async def get_by_username(self, username: str) -> SysUser | None:
        stmt = select(SysUser).where(
            SysUser.username == username, SysUser.is_deleted.is_(False)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_users(self, query: UserQuery) -> tuple[list[SysUser], int]:
        stmt = select(SysUser).where(SysUser.is_deleted.is_(False))
        if query.keyword:
            like = f"%{query.keyword}%"
            stmt = stmt.where(
                or_(
                    SysUser.username.ilike(like),
                    SysUser.real_name.ilike(like),
                    SysUser.mobile.ilike(like),
                )
            )
        if query.status is not None:
            stmt = stmt.where(SysUser.status == query.status)
        if query.dept_id is not None:
            stmt = stmt.where(SysUser.dept_id == query.dept_id)
        stmt = stmt.order_by(SysUser.id.desc())
        return await self.paginate(stmt, query.page, query.page_size)

    async def list_permission_codes(self, user_id: int) -> list[str]:
        """查询用户拥有的全部权限码。"""
        stmt = (
            select(SysPermission.perm_code)
            .join(
                SysRolePermission,
                SysRolePermission.permission_id == SysPermission.id,
            )
            .join(SysUserRole, SysUserRole.role_id == SysRolePermission.role_id)
            .where(SysUserRole.user_id == user_id)
            .distinct()
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        return [code for code in rows if code]

    async def list_role_codes(self, user_id: int) -> list[str]:
        """查询用户关联的角色 code 列表。"""
        stmt = (
            select(SysRole.code)
            .join(SysUserRole, SysUserRole.role_id == SysRole.id)
            .where(SysUserRole.user_id == user_id)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_visible_menus(self, user_id: int) -> list[SysPermission]:
        """查询用户角色可见的目录（type=1）与菜单（type=2）权限节点。"""
        stmt = (
            select(SysPermission)
            .join(SysRolePermission, SysRolePermission.permission_id == SysPermission.id)
            .join(SysUserRole, SysUserRole.role_id == SysRolePermission.role_id)
            .where(
                SysUserRole.user_id == user_id,
                SysPermission.type.in_((1, 2)),
                SysPermission.is_deleted.is_(False),
            )
            .distinct()
            .order_by(SysPermission.sort, SysPermission.id)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def replace_roles(self, user_id: int, role_ids: list[int]) -> None:
        """重建用户的角色关联。"""
        await self.db.execute(delete(SysUserRole).where(SysUserRole.user_id == user_id))
        for role_id in dict.fromkeys(role_ids):
            self.db.add(SysUserRole(user_id=user_id, role_id=role_id))
        await self.db.flush()
