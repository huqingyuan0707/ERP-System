"""销售报价数据访问（SA-01）：单号取号锁、单号查重、列表筛选与明细批量查询。

约定：
  - 继承 BaseRepository，租户过滤统一走 _apply_tenant_filter，分页用 paginate；
  - 不抛业务异常、不自行提交事务（事务边界在 Service）；
  - 列表页批量补齐客户名称、明细行数，避免 N+1；
  - 详情页批量加载明细，不逐行查询。
"""

from sqlalchemy import Select, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base_data import BdCustomer
from app.models.sal import SalQuotation, SalQuotationItem
from app.repositories.base import BaseRepository
from app.schemas.sal_quotation import SalQuotationQuery


class SalQuotationRepository(BaseRepository[SalQuotation]):
    """封装 sal_quotation / sal_quotation_item 读写。"""

    model = SalQuotation

    def __init__(self, db: AsyncSession, tenant_id: int | None = None) -> None:
        super().__init__(db, tenant_id=tenant_id)

    async def lock_for_write(self) -> None:
        """取号与单号查重共用一把事务级 advisory 锁，避免并发生成重复单号。

        PostgreSQL 下用 pg_advisory_xact_lock 串行化；SQLite（测试库）无此函数，直接跳过，
        由 uq_sal_quotation_no 唯一索引兜底。
        """
        if self.db.get_bind().dialect.name == "postgresql":
            await self.db.execute(
                select(func.pg_advisory_xact_lock(func.hashtext("sal_quotation")))
            )

    async def get_with_items(
        self, quotation_id: int, *, for_update: bool = False
    ) -> SalQuotation | None:
        """按主键取报价单；明细由 list_items 批量查询（SalQuotation 未定义 relationship）。

        for_update=True 时对主表加行锁：发送/作废/修改/删除都走这里，
        保证「读状态 → 校验 → 改状态」不会被并发请求穿插。
        使用 populate_existing=True 强制从库中取最新值，避免会话里已缓存的对象
        在 flush 后仍返回旧状态（否则发送后回查会读到旧 status）。
        """
        stmt = (
            select(SalQuotation)
            .where(SalQuotation.id == quotation_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            stmt = stmt.with_for_update()
        stmt = self._apply_tenant_filter(stmt)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_items(
        self, quotation_ids: list[int]
    ) -> dict[int, list[SalQuotationItem]]:
        """按单据 ID 批量查明细（含已软删行，供内部状态判断），一次 IN 查询避免 N+1。"""
        if not quotation_ids:
            return {}
        # 显式 select_from(sal_quotation_item)：TenantMixin 反向引用时不指定 FROM
        # 会把两张表都放进 FROM，产生笛卡尔积告警
        stmt = (
            select(SalQuotationItem)
            .select_from(SalQuotationItem)
            .where(SalQuotationItem.quotation_id.in_(quotation_ids))
            .order_by(SalQuotationItem.id)
        )
        if self.tenant_id is not None:
            # 明细表自带 tenant_id，直接过滤；不用 _apply_tenant_filter 以免再次引入主表
            stmt = stmt.where(SalQuotationItem.tenant_id == self.tenant_id)
        grouped: dict[int, list[SalQuotationItem]] = {}
        for item in (await self.db.execute(stmt)).scalars().all():
            grouped.setdefault(int(item.quotation_id), []).append(item)
        return grouped

    async def get_by_no(self, quote_no: str) -> SalQuotation | None:
        """按单号查重（含软删记录，与唯一索引口径一致，软删单号不复用）。"""
        stmt = select(SalQuotation).where(SalQuotation.quote_no == quote_no)
        stmt = self._apply_tenant_filter(stmt)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    def build_query(self, query: SalQuotationQuery) -> Select:
        """组合状态/报价日期区间/关键字（报价号/客户名称）筛选；租户过滤由分页方法追加。

        客户名称关键字用 EXISTS 子查询匹配，不 join 主查询，
        避免影响 paginate 的 count 子查询与实体列提取。
        """
        stmt = select(SalQuotation).where(SalQuotation.is_deleted.is_(False))
        if query.status is not None:
            stmt = stmt.where(SalQuotation.status == query.status)
        if query.quote_date_start is not None:
            stmt = stmt.where(SalQuotation.quote_date >= query.quote_date_start)
        if query.quote_date_end is not None:
            stmt = stmt.where(SalQuotation.quote_date <= query.quote_date_end)
        if query.keyword:
            like = f"%{query.keyword}%"
            customer_exists = (
                select(BdCustomer.id)
                .where(
                    BdCustomer.id == SalQuotation.customer_id,
                    BdCustomer.name.ilike(like),
                )
                .exists()
            )
            stmt = stmt.where(
                or_(SalQuotation.quote_no.ilike(like), exists(customer_exists))
            )
        # 单号倒序：最新报价排在最前，符合业务人员查看习惯
        return stmt.order_by(SalQuotation.quote_no.desc(), SalQuotation.id.desc())

    async def soft_delete_items(self, quotation_id: int, operator_id: int) -> None:
        """明细整体替换/删除单据前的软删：UPDATE is_deleted，保留历史行便于追溯。

        表名显式指定，避免 UPDATE 语句里出现隐式 FROM 产生笛卡尔积。
        """
        await self.db.execute(
            update(SalQuotationItem)
            .where(
                SalQuotationItem.quotation_id == quotation_id,
                SalQuotationItem.is_deleted.is_(False),
            )
            .values(is_deleted=True, updated_by=operator_id)
        )
