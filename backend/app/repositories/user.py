"""用户数据访问层（Repository）。

本文件是 Repository 层，只负责“和数据库打交道”，不包含业务规则。
Service 层（services/user.py）会调用这里的方法来查询、插入、更新、删除数据。

设计原则：
  - 这里只写 SQL / ORM 操作，不写业务判断（比如“账号是否唯一”的业务判断在 Service 层）；
  - 所有方法都是异步的（async/await），使用 SQLAlchemy 2.0 异步会话 AsyncSession；
  - 继承自通用 BaseRepository，复用分页、新增、删除等通用能力。
"""

from sqlalchemy import delete, or_, select

from app.models.sys import (
    SysDept,
    SysPermission,
    SysRole,
    SysRolePermission,
    SysUser,
    SysUserRole,
)
from app.repositories.base import BaseRepository
from app.schemas.user import UserQuery


class UserRepository(BaseRepository[SysUser]):
    """用户仓储类。

    继承自 BaseRepository[SysUser]，model 指定为 SysUser。
    BaseRepository 已经提供了：
      - get(pk)：按主键查一条
      - add(obj)：新增一条并 flush
      - delete(obj)：删除一条
      - paginate(stmt, page, page_size)：分页查询，返回 (列表, 总数)
    """

    model = SysUser

    # ------------------------------------------------------------------
    # 单条查询
    # ------------------------------------------------------------------

    async def get_by_username(self, username: str) -> SysUser | None:
        """根据登录账号查询用户。

        用途：登录时校验账号是否存在、新增用户时校验账号是否重复。
        查询条件：用户名匹配 且 未被软删除（is_deleted = False）。

        返回：
          - SysUser 对象：找到且未删除
          - None：未找到或已被软删除
        """
        stmt = select(SysUser).where(
            SysUser.username == username,
            SysUser.is_deleted.is_(False),
        )
        if self.tenant_id is not None:
            stmt = stmt.where(SysUser.tenant_id == self.tenant_id)  # 租户隔离：只查当前租户用户
        # scalar_one_or_none()：返回 0 或 1 条结果，多于 1 条会报错
        return (await self.db.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------
    # 列表查询（分页 + 筛选 + 模糊搜索）
    # ------------------------------------------------------------------

    async def list_users(self, query: UserQuery) -> tuple[list[SysUser], int]:
        """用户列表分页查询。

        参数：
          - query：UserQuery，包含 page、page_size、keyword、status、dept_id

        返回：
          - (rows, total)：当前页的用户对象列表，以及符合条件的总行数

        查询逻辑：
          1. 基础条件：is_deleted = False（软删除的用户不出现在列表里）
          2. keyword：如果传了关键词，匹配 用户名 / 真实姓名 / 手机号（模糊匹配，不区分大小写）
          3. status：如果传了状态，精确匹配启用/禁用
          4. dept_id：如果传了部门 ID，精确匹配所属部门
          5. 排序：按 ID 降序， newest first
          6. 分页：调用父类 paginate 方法，自动算出总数并切分当前页数据
        """
        stmt = select(SysUser).where(SysUser.is_deleted.is_(False))
        if self.tenant_id is not None:
            stmt = stmt.where(SysUser.tenant_id == self.tenant_id)  # 租户隔离：只查当前租户用户

        if query.keyword:
            like = f"%{query.keyword}%"
            stmt = stmt.where(
                or_(
                    SysUser.username.ilike(like),   # 用户名模糊匹配（不区分大小写）
                    SysUser.real_name.ilike(like),  # 真实姓名模糊匹配
                    SysUser.mobile.ilike(like),     # 手机号模糊匹配
                )
            )

        if query.status is not None:
            stmt = stmt.where(SysUser.status == query.status)

        if query.dept_id is not None:
            stmt = stmt.where(SysUser.dept_id == query.dept_id)

        stmt = stmt.order_by(SysUser.id.desc())  # 最新创建的排在前面
        return await self.paginate(stmt, query.page, query.page_size)

    # ------------------------------------------------------------------
    # 权限与菜单相关查询（供登录鉴权、前端动态路由使用）
    # ------------------------------------------------------------------

    async def list_permission_codes(self, user_id: int, tenant_id: int) -> list[str]:
        """查询用户通过当前租户启用角色拥有的有效权限码。"""
        stmt = (
            select(SysPermission.perm_code)
            .join(
                SysRolePermission,
                SysRolePermission.permission_id == SysPermission.id,
            )
            .join(SysRole, SysRole.id == SysRolePermission.role_id)
            .join(SysUserRole, SysUserRole.role_id == SysRolePermission.role_id)
            .join(SysUser, SysUser.id == SysUserRole.user_id)
            .where(
                SysUserRole.user_id == user_id,
                SysUser.tenant_id == tenant_id,
                SysUser.is_deleted.is_(False),
                SysRole.tenant_id == tenant_id,
                SysRole.status == 1,
                SysRole.is_deleted.is_(False),
                SysPermission.is_deleted.is_(False),
            )
            .distinct()
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        return [code for code in rows if code]

    async def list_role_codes(self, user_id: int, tenant_id: int) -> list[str]:
        """查询用户在当前租户关联的启用角色 code 列表。"""
        stmt = (
            select(SysRole.code)
            .join(SysUserRole, SysUserRole.role_id == SysRole.id)
            .join(SysUser, SysUser.id == SysUserRole.user_id)
            .where(
                SysUserRole.user_id == user_id,
                SysUser.tenant_id == tenant_id,
                SysUser.is_deleted.is_(False),
                SysRole.tenant_id == tenant_id,
                SysRole.status == 1,
                SysRole.is_deleted.is_(False),
            )
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_visible_menus(self, user_id: int, tenant_id: int) -> list[SysPermission]:
        """查询用户通过当前租户启用角色可见的有效目录与菜单。"""
        stmt = (
            select(SysPermission)
            .join(SysRolePermission, SysRolePermission.permission_id == SysPermission.id)
            .join(SysRole, SysRole.id == SysRolePermission.role_id)
            .join(SysUserRole, SysUserRole.role_id == SysRolePermission.role_id)
            .join(SysUser, SysUser.id == SysUserRole.user_id)
            .where(
                SysUserRole.user_id == user_id,
                SysUser.tenant_id == tenant_id,
                SysUser.is_deleted.is_(False),
                SysRole.tenant_id == tenant_id,
                SysRole.status == 1,
                SysRole.is_deleted.is_(False),
                SysPermission.type.in_((1, 2)),  # 1=目录，2=菜单，3=按钮（此处不取按钮）
                SysPermission.is_deleted.is_(False),
            )
            .distinct()
            .order_by(SysPermission.sort, SysPermission.id)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # 角色关联查询（多对多：用户 <-> 角色）
    # ------------------------------------------------------------------

    async def list_role_ids(self, user_id: int) -> list[int]:
        """查询当前租户用户已分配且未删除的角色 ID 列表。"""
        if self.tenant_id is None:
            return []
        stmt = (
            select(SysUserRole.role_id)
            .join(SysUser, SysUser.id == SysUserRole.user_id)
            .join(SysRole, SysRole.id == SysUserRole.role_id)
            .where(
                SysUserRole.user_id == user_id,
                SysUser.tenant_id == self.tenant_id,
                SysUser.is_deleted.is_(False),
                SysRole.tenant_id == self.tenant_id,
                SysRole.is_deleted.is_(False),
            )
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_role_ids_map(self, user_ids: list[int]) -> dict[int, list[int]]:
        """批量查询多个用户的角色 ID，返回 {user_id: [role_id, ...]}。

        用途：用户列表页一次性查出所有用户的角色，避免在循环里逐个查询（N+1 问题）。

        示例返回：{1: [1, 2], 2: [3], 3: []}
        """
        if not user_ids:
            return {}
        stmt = (
            select(SysUserRole.user_id, SysUserRole.role_id)
            .join(SysUser, SysUser.id == SysUserRole.user_id)
            .join(SysRole, SysRole.id == SysUserRole.role_id)
            .where(
                SysUserRole.user_id.in_(user_ids),
                SysUser.tenant_id == self.tenant_id,
                SysUser.is_deleted.is_(False),
                SysRole.tenant_id == self.tenant_id,
                SysRole.is_deleted.is_(False),
            )
        )
        mapping: dict[int, list[int]] = {}
        for user_id, role_id in (await self.db.execute(stmt)).all():
            mapping.setdefault(user_id, []).append(role_id)
        return mapping

    # ------------------------------------------------------------------
    # 存在性校验（供 Service 层校验前端传的部门/角色是否合法）
    # ------------------------------------------------------------------

    async def existing_role_ids(self, role_ids: list[int]) -> set[int]:
        """返回当前租户真实存在且未删除的角色 ID 集合。"""
        if not role_ids:
            return set()
        if self.tenant_id is None:
            return set()
        stmt = select(SysRole.id).where(
            SysRole.id.in_(role_ids),
            SysRole.tenant_id == self.tenant_id,
            SysRole.is_deleted.is_(False),
        )
        return set((await self.db.execute(stmt)).scalars().all())

    async def existing_dept_ids(self, dept_ids: list[int]) -> set[int]:
        """返回当前租户真实存在且未删除的部门 ID 集合。"""
        if not dept_ids:
            return set()
        if self.tenant_id is None:
            return set()
        stmt = select(SysDept.id).where(
            SysDept.id.in_(dept_ids),
            SysDept.tenant_id == self.tenant_id,
            SysDept.is_deleted.is_(False),
        )
        return set((await self.db.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # 角色关联写操作（多对多中间表 sys_user_role）
    # ------------------------------------------------------------------

    async def replace_roles(self, user_id: int, role_ids: list[int]) -> None:
        """重建当前租户用户的角色关联。"""
        if self.tenant_id is None:
            raise ValueError("tenant_id is required to replace user roles")
        user_exists = await self.db.scalar(
            select(SysUser.id).where(
                SysUser.id == user_id,
                SysUser.tenant_id == self.tenant_id,
                SysUser.is_deleted.is_(False),
            )
        )
        if user_exists is None:
            raise ValueError("user does not belong to current tenant")
        if set(role_ids) != await self.existing_role_ids(role_ids):
            raise ValueError("role does not belong to current tenant")
        await self.db.execute(delete(SysUserRole).where(SysUserRole.user_id == user_id))
        for role_id in dict.fromkeys(role_ids):
            self.db.add(SysUserRole(user_id=user_id, role_id=role_id))
        await self.db.flush()
