"""员工数据访问（B-06）：编码取号、查重、分页筛选与用户占用查询。"""

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base_data import BdEmployee
from app.repositories.base import BaseRepository
from app.schemas.employee import EmployeeQuery


class EmployeeRepository(BaseRepository[BdEmployee]):
    """封装 bd_employee 读写，不抛业务异常、不自行提交事务。"""

    model = BdEmployee

    def __init__(self, db: AsyncSession, tenant_id: int | None = None) -> None:
        super().__init__(db, tenant_id=tenant_id)

    async def lock_for_write(self) -> None:
        """取号与查重共用一把事务锁，避免并发生成重复编码。"""
        if self.db.get_bind().dialect.name == "postgresql":
            await self.db.execute(
                select(func.pg_advisory_xact_lock(func.hashtext("bd_employee")))
            )

    async def get_for_update(self, employee_id: int) -> BdEmployee | None:
        stmt = (
            select(BdEmployee)
            .where(BdEmployee.id == employee_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        stmt = self._apply_tenant_filter(stmt)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_code(self, code: str, exclude_id: int | None = None) -> BdEmployee | None:
        """编码查重（含软删记录，与唯一索引一致）。"""
        stmt = select(BdEmployee).where(BdEmployee.code == code)
        stmt = self._apply_tenant_filter(stmt)
        if exclude_id is not None:
            stmt = stmt.where(BdEmployee.id != exclude_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_user(self, user_id: int, exclude_id: int | None = None) -> BdEmployee | None:
        """检查系统账号是否已被其他员工关联（含软删记录）。"""
        stmt = select(BdEmployee).where(BdEmployee.user_id == user_id)
        stmt = self._apply_tenant_filter(stmt)
        if exclude_id is not None:
            stmt = stmt.where(BdEmployee.id != exclude_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def linked_user_ids(self) -> dict[int, int]:
        """返回 user_id -> employee_id 映射，供关联用户下拉排除已占用账号。"""
        stmt = select(BdEmployee.user_id, BdEmployee.id).where(
            BdEmployee.user_id.is_not(None)
        )
        stmt = self._apply_tenant_filter(stmt)
        rows = (await self.db.execute(stmt)).all()
        return {int(row[0]): int(row[1]) for row in rows}

    async def max_code(self, prefix: str) -> str | None:
        """返回 prefix 下数字序号最大的编码；自定义编码不参与取号。"""
        stmt = self._apply_tenant_filter(
            select(BdEmployee.code).where(BdEmployee.code.like(f"{prefix}%"))
        )
        codes = (await self.db.execute(stmt)).scalars().all()
        numeric = [
            code
            for code in codes
            if (suffix := code.removeprefix(prefix)).isascii() and suffix.isdecimal()
        ]
        return max(numeric, key=lambda code: int(code[len(prefix) :]), default=None)

    def build_query(
        self, query: EmployeeQuery, dept_ids: set[int] | None = None
    ) -> Select[tuple[BdEmployee]]:
        """组合关键字、状态、部门（含子树）筛选；租户过滤由父类分页追加。"""
        stmt = select(BdEmployee).where(BdEmployee.is_deleted.is_(False))
        if query.keyword:
            like = f"%{query.keyword}%"
            stmt = stmt.where(
                or_(
                    BdEmployee.code.ilike(like),
                    BdEmployee.name.ilike(like),
                    BdEmployee.position.ilike(like),
                    BdEmployee.mobile.ilike(like),
                )
            )
        if query.status is not None:
            stmt = stmt.where(BdEmployee.status == query.status)
        if dept_ids is not None:
            stmt = stmt.where(BdEmployee.dept_id.in_(dept_ids))
        elif query.dept_id is not None:
            stmt = stmt.where(BdEmployee.dept_id == query.dept_id)
        return self._apply_tenant_filter(stmt).order_by(BdEmployee.code.asc())

    async def list_selectable(self) -> list[BdEmployee]:
        """仅启用员工；停用员工不出现在新单据的可选列表中，历史单据不受影响。"""
        stmt = select(BdEmployee).where(
            BdEmployee.is_deleted.is_(False), BdEmployee.status == 1
        )
        stmt = self._apply_tenant_filter(stmt).order_by(BdEmployee.code.asc())
        return list((await self.db.execute(stmt)).scalars().all())
