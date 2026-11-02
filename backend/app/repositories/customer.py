"""客户数据访问：封装查询、编码取号与单据引用检查，业务判断与事务提交由 Service 负责。"""

from decimal import Decimal

from sqlalchemy import func, or_, select, text

from app.models.base_data import BdCustomer
from app.models.finance import ArApRecord
from app.repositories.base import BaseRepository
from app.schemas.customer import CustomerQuery


CUSTOMER_CODE_PREFIX = "CUS"

# 应收记录中业务方类型：2=客户（应收）
RECEIVABLE_PARTNER_TYPE_CUSTOMER = 2
# 应收记录类型：2=应收（区别于应付）
RECEIVABLE_RECORD_TYPE = 2

# 已结清的销售订单状态；其余状态都算未结，客户仍被业务单据引用
CLOSED_ORDER_STATUSES = ("完成", "关闭")

# 引用检查涉及的外部表；由销售模块创建，尚未创建时跳过对应检查
SALES_ORDER_TABLE = "sal_order"  # 销售订单表
SALES_OUTBOUND_TABLE = "sal_outbound"  # 销售出库表


class CustomerRepository(BaseRepository[BdCustomer]):
    """封装客户及外部单据的读写，不直接提交事务或抛出业务异常。"""

    model = BdCustomer

    def __init__(self, db, tenant_id: int | None = None):
        super().__init__(db, tenant_id=tenant_id)

    async def lock_for_write(self) -> None:
        """取得当前事务的客户写锁，提交或回滚时由数据库自动释放。"""
        if self.db.get_bind().dialect.name == "postgresql":  # 生产库使用事务级 advisory 锁
            await self.db.execute(
                select(func.pg_advisory_xact_lock(func.hashtext("bd_customer")))
            )

    async def get_for_update(self, customer_id: int) -> BdCustomer | None:
        """按 customer_id 返回最新客户或 None，并持有行锁至事务结束；包含软删除记录。"""
        stmt = (
            select(BdCustomer)
            .where(BdCustomer.id == customer_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        stmt = self._apply_tenant_filter(stmt)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def max_code(self, prefix: str) -> str | None:
        """返回 prefix 下数字序号最大的编码或 None；含软删除记录，忽略非数字自定义编码。"""
        stmt = self._apply_tenant_filter(
            select(BdCustomer.code).where(BdCustomer.code.like(f"{prefix}%"))
        )
        codes = (await self.db.execute(stmt)).scalars().all()
        numeric_codes = [
            code
            for code in codes
            if (suffix := code.removeprefix(prefix)).isascii() and suffix.isdecimal()
        ]
        return max(numeric_codes, key=lambda code: int(code[len(prefix) :]), default=None)

    async def get_by_code(self, code: str, exclude_id: int | None = None) -> BdCustomer | None:
        """按 code 查重；exclude_id 可排除当前客户，便于编辑时保持原编码。"""
        stmt = select(BdCustomer).where(BdCustomer.code == code)
        stmt = self._apply_tenant_filter(stmt)
        if exclude_id is not None:
            stmt = stmt.where(BdCustomer.id != exclude_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    def build_query(self, query: CustomerQuery):
        """根据 keyword、status 构造未删除客户查询；返回 SQL，交给父类分页执行。"""
        stmt = select(BdCustomer).where(BdCustomer.is_deleted.is_(False))
        if query.keyword:
            like = f"%{query.keyword}%"
            stmt = stmt.where(
                or_(
                    BdCustomer.code.ilike(like),
                    BdCustomer.name.ilike(like),
                    BdCustomer.contact.ilike(like),
                    BdCustomer.phone.ilike(like),
                )
            )
        if query.status is not None:
            stmt = stmt.where(BdCustomer.status == query.status)
        return self._apply_tenant_filter(stmt).order_by(BdCustomer.id.desc())

    async def list_selectable(self) -> list[BdCustomer]:
        """返回启用状态的客户；停用客户不会出现在新单据的可选列表中。"""
        stmt = (
            select(BdCustomer)
            .where(BdCustomer.is_deleted.is_(False))
            .where(BdCustomer.status == 1)
            .order_by(BdCustomer.code.asc())
        )
        stmt = self._apply_tenant_filter(stmt)
        return list((await self.db.execute(stmt)).scalars().all())

    async def existing_tables(self) -> set[str]:
        """返回当前数据库实际存在的表名集合；外部单据表未建好时跳过对应检查。"""
        dialect = self.db.get_bind().dialect.name
        if dialect == "postgresql":
            sql = (
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema()"
            )
        elif dialect == "sqlite":
            sql = "SELECT name FROM sqlite_master WHERE type = 'table'"
        else:
            return set()
        rows = (await self.db.execute(text(sql))).scalars().all()
        return {str(name) for name in rows}

    async def has_sales_order(self, customer_id: int) -> bool:
        """返回该客户是否存在未结清销售订单；销售订单表未创建时返回 False。"""
        if SALES_ORDER_TABLE not in await self.existing_tables():
            return False
        closed = ", ".join(f"'{status}'" for status in CLOSED_ORDER_STATUSES)
        tenant_clause = (
            f"AND tenant_id = {self.tenant_id}" if self.tenant_id is not None else ""
        )
        stmt = text(
            f"SELECT 1 FROM {SALES_ORDER_TABLE} "
            "WHERE customer_id = :customer_id "
            "AND is_deleted IS NOT TRUE "
            f"{tenant_clause} "
            f"AND status NOT IN ({closed}) LIMIT 1"
        )
        result = await self.db.execute(stmt, {"customer_id": customer_id})
        return result.first() is not None

    async def has_outbound(self, customer_id: int) -> bool:
        """返回该客户是否存在销售出库单；出库表未创建时返回 False。"""
        if SALES_OUTBOUND_TABLE not in await self.existing_tables():
            return False
        tenant_clause = (
            f"AND tenant_id = {self.tenant_id}" if self.tenant_id is not None else ""
        )
        stmt = text(
            f"SELECT 1 FROM {SALES_OUTBOUND_TABLE} "
            "WHERE customer_id = :customer_id "
            "AND is_deleted IS NOT TRUE "
            f"{tenant_clause} LIMIT 1"
        )
        result = await self.db.execute(stmt, {"customer_id": customer_id})
        return result.first() is not None

    async def has_receivable(self, customer_id: int) -> bool:
        """返回该客户是否存在未作废应收记录（fin_ar_ap，partner_type=2/record_type=2）。"""
        stmt = (
            select(ArApRecord.id)
            .where(ArApRecord.partner_type == RECEIVABLE_PARTNER_TYPE_CUSTOMER)
            .where(ArApRecord.record_type == RECEIVABLE_RECORD_TYPE)
            .where(ArApRecord.partner_id == customer_id)
            .where(ArApRecord.status != 4)
        )
        if self.tenant_id is not None:
            stmt = stmt.where(ArApRecord.tenant_id == self.tenant_id)
        result = await self.db.execute(stmt.limit(1))
        return result.first() is not None

    async def sum_unpaid_receivable(self, customer_ids: list[int]) -> dict[int, Decimal]:
        """按 customer_ids 汇总未核销应收余额（fin_ar_ap，partner_type=2/record_type=2）。

        未核销金额 = amount - reconciled_amount，已核销部分不计入；作废（status=4）不计。
        """
        if not customer_ids:
            return {}
        stmt = (
            select(
                ArApRecord.partner_id,
                func.coalesce(func.sum(ArApRecord.amount - ArApRecord.reconciled_amount), 0),
            )
            .where(ArApRecord.partner_type == RECEIVABLE_PARTNER_TYPE_CUSTOMER)
            .where(ArApRecord.record_type == RECEIVABLE_RECORD_TYPE)
            .where(ArApRecord.partner_id.in_(customer_ids))
            .where(ArApRecord.status != 4)
            .group_by(ArApRecord.partner_id)
        )
        if self.tenant_id is not None:
            stmt = stmt.where(ArApRecord.tenant_id == self.tenant_id)
        rows = (await self.db.execute(stmt)).all()
        return {int(row[0]): Decimal(str(row[1] or 0)) for row in rows}
