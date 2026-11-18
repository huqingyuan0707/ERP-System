"""付款数据访问（F-04）：列表筛选、行锁、核销明细批量查询与供应商名称批量查询。

约定（与 PurReturnRepository / PayableRepository 保持一致）：
  - 继承 BaseRepository，租户过滤统一走 _apply_tenant_filter，分页用 paginate；
  - 不抛业务异常、不自行提交事务（事务边界在 Service）；
  - FinPayment 未定义 relationship，明细用 list_items 批量 IN 查询，避免 N+1。
"""

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base_data import BdSupplier
from app.models.finance import FinPayable, FinPayment, FinPaymentItem
from app.repositories.base import BaseRepository
from app.schemas.payment import PaymentQuery, PaymentStatus


class PaymentRepository(BaseRepository[FinPayment]):
    """封装 fin_payment / fin_payment_item 读写与核销口径查询。"""

    model = FinPayment

    def __init__(self, db: AsyncSession, tenant_id: int | None = None) -> None:
        super().__init__(db, tenant_id=tenant_id)

    async def get_with_lock(
        self, payment_id: int, *, for_update: bool = False
    ) -> FinPayment | None:
        """按主键取付款单；for_update=True 时加行锁，用于修改/删除/核销的状态判断。

        populate_existing=True 强制从库中取最新值，避免会话缓存导致读到旧状态。
        """
        stmt = (
            select(FinPayment)
            .where(FinPayment.id == payment_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            stmt = stmt.with_for_update()
        stmt = self._apply_tenant_filter(stmt)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_payable_for_update(self, payable_id: int) -> FinPayable | None:
        """按主键定位应付并加行锁，供核销前校验与扣减（防两张付款单并发丢失更新）。

        查询主表是 fin_payable，不能用 _apply_tenant_filter（它按本类
        model=fin_payment 过滤租户会造成笛卡尔积），租户条件显式写在 WHERE。
        """
        stmt = (
            select(FinPayable)
            .where(
                FinPayable.id == payable_id,
                FinPayable.tenant_id == self.tenant_id,
                FinPayable.is_deleted.is_(False),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_items(self, payment_ids: list[int]) -> dict[int, list[FinPaymentItem]]:
        """按付款单 ID 批量查核销明细，一次 IN 查询避免 N+1。"""
        if not payment_ids:
            return {}
        # 显式 select_from：FinPaymentItem 关联 fin_payment/fin_payable，
        # 不指定 FROM 会因外键反向引用触发笛卡尔积告警
        stmt = (
            select(FinPaymentItem)
            .select_from(FinPaymentItem)
            .where(FinPaymentItem.payment_id.in_(payment_ids))
            .order_by(FinPaymentItem.id)
        )
        if self.tenant_id is not None:
            # 明细表自带 tenant_id，直接过滤；不用 _apply_tenant_filter 以免引入主表
            stmt = stmt.where(FinPaymentItem.tenant_id == self.tenant_id)
        grouped: dict[int, list[FinPaymentItem]] = {}
        for item in (await self.db.execute(stmt)).scalars().all():
            grouped.setdefault(int(item.payment_id), []).append(item)
        return grouped

    async def map_payable_source_nos(self, payable_ids: set[int]) -> dict[int, str]:
        """按应付 ID 批量解析原入库单号（含软删口径，纯展示用）。"""
        if not payable_ids:
            return {}
        stmt = select(FinPayable.id, FinPayable.source_no).where(
            FinPayable.id.in_(payable_ids),
            FinPayable.tenant_id == self.tenant_id,
        )
        rows = (await self.db.execute(stmt)).all()
        return {int(row[0]): str(row[1]) for row in rows}

    def build_query(self, query: PaymentQuery) -> Select:
        """组合供应商/核销进度/付款日期/关键字筛选；租户与数据范围由 Service 追加。

        核销进度无状态列，按 allocated_amount 与 amount 的比较翻译成 SQL 条件：
        0 待核销（allocated<=0）/ 1 部分核销（0<allocated<amount）/ 2 已核销（allocated>=amount）。
        """
        stmt = select(FinPayment).where(FinPayment.is_deleted.is_(False))
        if query.supplier_id is not None:
            stmt = stmt.where(FinPayment.supplier_id == query.supplier_id)
        if query.status == PaymentStatus.WAIT:
            stmt = stmt.where(FinPayment.allocated_amount <= 0)
        elif query.status == PaymentStatus.PARTIAL:
            stmt = stmt.where(
                FinPayment.allocated_amount > 0, FinPayment.allocated_amount < FinPayment.amount
            )
        elif query.status == PaymentStatus.DONE:
            stmt = stmt.where(FinPayment.allocated_amount >= FinPayment.amount)
        if query.payment_date_start is not None:
            stmt = stmt.where(FinPayment.payment_date >= query.payment_date_start)
        if query.payment_date_end is not None:
            stmt = stmt.where(FinPayment.payment_date <= query.payment_date_end)
        if query.keyword:
            like = f"%{query.keyword}%"
            stmt = stmt.where(
                or_(
                    FinPayment.payment_no.ilike(like),
                    FinPayment.remark.ilike(like),
                )
            )
        return stmt.order_by(FinPayment.payment_date.desc(), FinPayment.id.desc())

    async def supplier_names(self, ids: list[int]) -> dict[int, str]:
        """批量取供应商名称，避免逐行查询。"""
        if not ids:
            return {}
        stmt = select(BdSupplier.id, BdSupplier.name).where(
            BdSupplier.id.in_(ids), BdSupplier.is_deleted.is_(False)
        )
        if self.tenant_id is not None:
            stmt = stmt.where(BdSupplier.tenant_id == self.tenant_id)
        return {int(row[0]): str(row[1]) for row in (await self.db.execute(stmt)).all()}
