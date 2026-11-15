"""部门数据访问（B-06）：树查询、行锁、引用计数与子树展开。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base_data import BdEmployee
from app.models.sys import SysDept, SysRoleDept, SysUser
from app.repositories.base import BaseRepository


class DeptRepository(BaseRepository[SysDept]):
    """封装 sys_dept 读写，不抛业务异常、不自行提交事务。"""

    model = SysDept

    def __init__(self, db: AsyncSession, tenant_id: int | None = None) -> None:
        super().__init__(db, tenant_id=tenant_id)

    async def lock_tree(self) -> None:
        """PostgreSQL 下用事务 advisory 锁串行化树写操作；SQLite 测试库跳过。"""
        if self.db.get_bind().dialect.name == "postgresql":
            await self.db.execute(
                select(func.pg_advisory_xact_lock(func.hashtext("sys_dept")))
            )

    async def get_for_update(self, dept_id: int) -> SysDept | None:
        stmt = (
            select(SysDept)
            .where(SysDept.id == dept_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        stmt = self._apply_tenant_filter(stmt)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> list[SysDept]:
        """当前租户未删除部门，按 sort、id 升序。"""
        stmt = select(SysDept).where(SysDept.is_deleted.is_(False))
        stmt = self._apply_tenant_filter(stmt).order_by(SysDept.sort, SysDept.id)
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_by_name(self, name: str, exclude_id: int | None = None) -> SysDept | None:
        """按名称查重（含软删记录，与唯一索引一致）。"""
        stmt = select(SysDept).where(SysDept.name == name)
        stmt = self._apply_tenant_filter(stmt)
        if exclude_id is not None:
            stmt = stmt.where(SysDept.id != exclude_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def subtree_ids(self, root_id: int) -> set[int]:
        """返回根部门及全部未删除子孙部门 id，供「含下级」员工筛选。"""
        found: set[int] = {root_id}
        frontier = [root_id]
        while frontier:
            stmt = select(SysDept.id).where(
                SysDept.parent_id.in_(frontier),
                SysDept.is_deleted.is_(False),
            )
            stmt = self._apply_tenant_filter(stmt)
            children = {int(value) for value in (await self.db.execute(stmt)).scalars().all()}
            fresh = children - found
            if not fresh:
                break
            found |= fresh
            frontier = list(fresh)
        return found

    async def reference_counts(self) -> dict[int, tuple[int, int, int]]:
        """批量统计各部门的子部门、员工、用户数，避免列表逐条查询。"""
        children = await self._group_count(SysDept.parent_id, SysDept)
        employees = await self._group_count(BdEmployee.dept_id, BdEmployee)
        users = await self._group_count(SysUser.dept_id, SysUser)
        return {
            dept_id: (children.get(dept_id, 0), employees.get(dept_id, 0), users.get(dept_id, 0))
            for dept_id in set(children) | set(employees) | set(users)
            if dept_id is not None
        }

    async def _group_count(self, column, model) -> dict[int | None, int]:
        stmt = select(column, func.count()).where(
            column.is_not(None), model.is_deleted.is_(False)
        )
        stmt = stmt.where(model.tenant_id == self.tenant_id).group_by(column)
        rows = (await self.db.execute(stmt)).all()
        return {int(row[0]): int(row[1]) for row in rows}

    async def count_references(self, dept_id: int) -> dict[str, int]:
        """删除前检查四类引用：子部门、员工、用户、角色数据范围。"""
        children = await self.db.scalar(
            select(func.count())
            .select_from(SysDept)
            .where(
                SysDept.parent_id == dept_id,
                SysDept.is_deleted.is_(False),
                SysDept.tenant_id == self.tenant_id,
            )
        )
        employees = await self.db.scalar(
            select(func.count())
            .select_from(BdEmployee)
            .where(
                BdEmployee.dept_id == dept_id,
                BdEmployee.is_deleted.is_(False),
                BdEmployee.tenant_id == self.tenant_id,
            )
        )
        users = await self.db.scalar(
            select(func.count())
            .select_from(SysUser)
            .where(
                SysUser.dept_id == dept_id,
                SysUser.is_deleted.is_(False),
                SysUser.tenant_id == self.tenant_id,
            )
        )
        role_dept = await self.db.scalar(
            select(func.count())
            .select_from(SysRoleDept)
            .where(SysRoleDept.dept_id == dept_id)
        )
        return {
            "children": int(children or 0),
            "employees": int(employees or 0),
            "users": int(users or 0),
            "role_dept": int(role_dept or 0),
        }
