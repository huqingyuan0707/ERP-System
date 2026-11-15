"""采购申请数据访问（P-01）：单号取号锁、单号查重、列表筛选与明细批量查询。

约定：
  - 继承 BaseRepository，租户过滤统一走 _apply_tenant_filter，分页用 paginate；
  - 不抛业务异常、不自行提交事务（事务边界在 Service）；
  - 列表页批量补齐部门/申请人名称、明细行数，避免 N+1；
  - 详情页用 selectinload 一把加载明细，不逐行查询。
"""

from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pur import PurApply, PurApplyItem
from app.repositories.base import BaseRepository
from app.schemas.purchase_apply import PurApplyQuery


class PurApplyRepository(BaseRepository[PurApply]):
    """封装 pur_apply / pur_apply_item 读写。"""

    model = PurApply

    def __init__(self, db: AsyncSession, tenant_id: int | None = None) -> None:
        super().__init__(db, tenant_id=tenant_id)

    async def lock_for_write(self) -> None:
        """取号与单号查重共用一把事务级 advisory 锁，避免并发生成重复单号。

        PostgreSQL 下用 pg_advisory_xact_lock 串行化；SQLite（测试库）无此函数，直接跳过，
        由 uq_pur_apply_no 唯一索引兜底。
        """
        if self.db.get_bind().dialect.name == "postgresql":
            await self.db.execute(select(func.pg_advisory_xact_lock(func.hashtext("pur_apply"))))

    async def get_with_items(self, apply_id: int, *, for_update: bool = False) -> PurApply | None:
        """按主键取申请单；明细由 list_items 批量查询（PurApply 未定义 relationship）。

        for_update=True 时对主表加行锁：提交/审核/修改/删除都走这里，
        保证「读状态 → 改状态」不会被并发请求穿插。
        使用 populate_existing=True 强制从库中取最新值，避免会话里已缓存的对象
        在 flush 后仍返回旧状态（否则审核后回查会读到旧 status）。
        """
        stmt = (
            select(PurApply)
            .where(PurApply.id == apply_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            stmt = stmt.with_for_update()
        stmt = self._apply_tenant_filter(stmt)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_items(self, apply_ids: list[int]) -> dict[int, list[PurApplyItem]]:
        """按单据 ID 批量查明细（含已软删行，供内部状态判断），一次 IN 查询避免 N+1。"""
        if not apply_ids:
            return {}
        # 显式 select_from(pur_apply_item)：purchase_apply 由 TenantMixin 反向引用，
        # 不指定 FROM 时 SQLAlchemy 会把两张表都放进 FROM，产生笛卡尔积告警
        stmt = (
            select(PurApplyItem)
            .select_from(PurApplyItem)
            .where(PurApplyItem.apply_id.in_(apply_ids))
            .order_by(PurApplyItem.id)
        )
        if self.tenant_id is not None:
            # 明细表自带 tenant_id，直接过滤；不用 _apply_tenant_filter 以免再次引入主表
            stmt = stmt.where(PurApplyItem.tenant_id == self.tenant_id)
        grouped: dict[int, list[PurApplyItem]] = {}
        for item in (await self.db.execute(stmt)).scalars().all():
            grouped.setdefault(int(item.apply_id), []).append(item)
        return grouped

    async def get_by_no(self, apply_no: str) -> PurApply | None:
        """按单号查重（含软删记录，与唯一索引口径一致，软删单号不复用）。"""
        stmt = select(PurApply).where(PurApply.apply_no == apply_no)
        stmt = self._apply_tenant_filter(stmt)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    def build_query(self, query: PurApplyQuery, dept_ids: set[int] | None = None) -> Select:
        """组合状态/部门/需求日期区间/关键字筛选；租户过滤由分页方法追加。

        dept_ids 为「按部门筛选时展开的部门集合」，为 None 时按单部门精确匹配。
        """
        stmt = select(PurApply).where(PurApply.is_deleted.is_(False))
        if query.status is not None:
            stmt = stmt.where(PurApply.status == query.status)
        if dept_ids is not None:
            stmt = stmt.where(PurApply.dept_id.in_(dept_ids))
        elif query.dept_id is not None:
            stmt = stmt.where(PurApply.dept_id == query.dept_id)
        if query.need_date_start is not None:
            stmt = stmt.where(PurApply.need_date >= query.need_date_start)
        if query.need_date_end is not None:
            stmt = stmt.where(PurApply.need_date <= query.need_date_end)
        if query.keyword:
            like = f"%{query.keyword}%"
            stmt = stmt.where(or_(PurApply.apply_no.ilike(like), PurApply.remark.ilike(like)))
        # 单号倒序：最新申请排在最前，符合业务人员查看习惯
        return stmt.order_by(PurApply.apply_no.desc(), PurApply.id.desc())

    async def subtree_dept_ids(self, root_id: int) -> set[int]:
        """返回申请部门及全部未删除子部门 ID，供「按部门（含下级）」筛选。

        逐层按 parent_id 展开，层数通常不超过 5，不会产生 N+1（每层一次 IN 查询）。
        """
        from app.models.sys import SysDept  # 局部导入：避免仓储层顶部循环引用

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

    async def soft_delete_items(self, apply_id: int, operator_id: int) -> None:
        """明细整体替换前的软删：UPDATE is_deleted，保留历史行便于追溯（不改历史数据）。

        表名显式指定，避免 UPDATE 语句里出现隐式 FROM 产生笛卡尔积。
        """
        await self.db.execute(
            update(PurApplyItem)
            .where(PurApplyItem.apply_id == apply_id, PurApplyItem.is_deleted.is_(False))
            .values(is_deleted=True, updated_by=operator_id)
        )

    async def purge_soft_deleted_items(self, apply_id: int) -> None:
        """彻底清理已软删的明细行（仅维护场景使用，正常业务不调用）。

        ERP 单据要求保留流水，故正常业务只做软删；本方法留给数据清理脚本，
        不在任何 HTTP 接口中暴露。
        """
        await self.db.execute(
            delete(PurApplyItem).where(
                PurApplyItem.apply_id == apply_id, PurApplyItem.is_deleted.is_(True)
            )
        )
