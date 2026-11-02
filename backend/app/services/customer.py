"""客户业务逻辑：编码、信用额度、应收余额、引用保护与操作日志。

提供信用额度校验与账期计算 helper，销售单据调用链留待对应模块接入。
信用额度 0 表示不限；有限额度按系统 block/warn/off 策略处理。
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ErrorCode
from app.models.base_data import BdCustomer
from app.repositories.customer import CustomerRepository
from app.schemas.common import PageResult
from app.schemas.customer import (
    CustomerCreate,
    CustomerCreditCheckOut,
    CustomerCreditOut,
    CustomerOut,
    CustomerQuery,
    CustomerUpdate,
)
from app.services.audit import write_oper_log
from app.services.config import ConfigService
from app.utils.code_gen import format_code, next_seq_from_code
from app.utils.pagination import paginate_result


CUSTOMER_CODE_PREFIX = "CUS"
OPER_MODULE = "基础资料"


class CustomerService:
    """提供客户查询和写操作；删除前检查销售与应收引用，停用只影响新单据选择。"""

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.customers = CustomerRepository(db, tenant_id=tenant_id)

    async def list_customers(self, query: CustomerQuery) -> PageResult[CustomerOut]:
        """按 query 返回未删除客户分页结果；应收余额批量汇总。"""
        stmt = self.customers.build_query(query)
        rows, total = await self.customers.paginate(stmt, query.page, query.page_size)
        receivable_map = await self.customers.sum_unpaid_receivable([row.id for row in rows])
        items = [
            CustomerOut.model_validate(row).model_copy(
                update={"receivable_amount": receivable_map.get(row.id, Decimal("0"))}
            )
            for row in rows
        ]
        return paginate_result(items, total, query.page, query.page_size)

    async def get_customer(self, customer_id: int) -> CustomerOut:
        """按 customer_id 返回详情和应收余额；不存在或已软删除时抛出 404。"""
        customer = await self._get_active_customer(customer_id)
        return await self._to_out(customer)

    async def list_selectable_customers(self) -> list[CustomerOut]:
        """返回启用状态的客户列表；停用客户不会出现在新单据的可选列表中。"""
        rows = await self.customers.list_selectable()
        receivable_map = await self.customers.sum_unpaid_receivable([row.id for row in rows])
        return [
            CustomerOut.model_validate(row).model_copy(
                update={"receivable_amount": receivable_map.get(row.id, Decimal("0"))}
            )
            for row in rows
        ]

    async def create_customer(
        self, payload: CustomerCreate, current_user_id: int, *, commit: bool = True
    ) -> CustomerOut:
        """用 payload 新建客户并记录 current_user_id；返回详情，编码重复抛 409。"""
        try:
            await self.customers.lock_for_write()
            code = (payload.code or "").strip() or await self._next_code()
            name = payload.name.strip()
            if not name:
                raise AppException(ErrorCode.VALIDATION_ERROR, "客户名称不能为空")
            await self._validate_unique(code)
            self._validate_credit_limit(payload.credit_limit)
            self._validate_credit_days(payload.credit_days)
            customer = BdCustomer(
                code=code,
                name=name,
                contact=payload.contact,
                phone=payload.phone,
                address=payload.address,
                tax_no=payload.tax_no,
                bank=payload.bank,
                account=payload.account,
                credit_limit=payload.credit_limit,
                credit_days=payload.credit_days,
                status=payload.status,
                remark=payload.remark,
                tenant_id=self.tenant_id,
                created_by=current_user_id,
                updated_by=current_user_id,
            )
            await self.customers.add(customer)
            await self._write_log(current_user_id, "新增客户", customer.id)
            result = await self._to_out(customer)
            if commit:
                await self.db.commit()
            return result
        except Exception:
            if commit:
                await self.db.rollback()
            raise

    async def update_customer(
        self,
        customer_id: int,
        payload: CustomerUpdate,
        current_user_id: int,
        *,
        commit: bool = True,
    ) -> CustomerOut:
        """将 payload 的已传字段更新到 customer_id，记录 current_user_id 后返回详情。

        不存在抛 404，必填列传空抛 422；编码重复抛 409；信用额度/账期为负抛 422。
        停用只改变新单据的可选范围，不影响历史单据。
        """
        try:
            await self.customers.lock_for_write()
            customer = await self._get_active_customer(customer_id, for_update=True)
            data: dict = payload.model_dump(exclude_unset=True)
            for field in ("code", "name", "credit_limit", "credit_days", "status"):
                if field in data and data[field] is None:
                    raise AppException(ErrorCode.VALIDATION_ERROR, f"{field} 不能为 null")
            for field in ("code", "name"):
                value = data.get(field)
                if isinstance(value, str):
                    value = value.strip()
                    if not value:
                        raise AppException(ErrorCode.VALIDATION_ERROR, f"{field} 不能为空")
                    data[field] = value
                    if field == "code":
                        await self._validate_unique(value, exclude_id=customer.id)
            if "credit_limit" in data:
                self._validate_credit_limit(data["credit_limit"])
            if "credit_days" in data:
                self._validate_credit_days(data["credit_days"])
            action = "修改客户"
            if "status" in data and data["status"] != customer.status:
                action = "启用客户" if data["status"] == 1 else "停用客户"
            for field, value in data.items():
                setattr(customer, field, value)
            customer.updated_by = current_user_id
            await self._write_log(current_user_id, action, customer.id)
            result = await self._to_out(customer)
            if commit:
                await self.db.commit()
            return result
        except Exception:
            if commit:
                await self.db.rollback()
            raise

    async def delete_customer(
        self, customer_id: int, current_user_id: int, *, commit: bool = True
    ) -> None:
        """删除 customer_id 并记录 current_user_id，无返回值。

        客户不存在抛 404；存在未结销售订单、销售出库单或应收记录时抛 409。
        """
        try:
            await self.customers.lock_for_write()
            customer = await self._get_active_customer(customer_id, for_update=True)
            await self._assert_can_delete(customer)
            customer.is_deleted = True
            customer.updated_by = current_user_id
            await self._write_log(current_user_id, "删除客户", customer.id)
            if commit:
                await self.db.commit()
        except Exception:
            if commit:
                await self.db.rollback()
            raise

    async def calculate_due_date(self, customer_id: int, bill_date: date) -> date:
        """按客户账期计算应收到期日，保留纯 helper，销售出库单据调用链尚待接入。

        账期为 0 时到期日与单据日期相同；停用客户同样可以计算历史单据的到期日。
        """
        customer = await self._get_active_customer(customer_id)
        return bill_date + timedelta(days=customer.credit_days)

    async def assert_within_credit(
        self, customer_id: int, amount: Decimal
    ) -> CustomerCreditCheckOut:
        """额度校验 helper；销售模块接入时须展示 warning，当前尚无单据调用链。"""
        summary = await self.get_credit_summary(customer_id)
        amount = Decimal(str(amount))
        if not amount.is_finite() or amount < 0:
            raise AppException(ErrorCode.VALIDATION_ERROR, "本次金额必须是非负有限数")
        exceeded = (
            not summary.unlimited and summary.receivable_amount + amount > summary.credit_limit
        )
        warning = None
        if exceeded and summary.mode != "off":
            message = (
                f"超出客户信用额度：已发生应收 {summary.receivable_amount}，"
                f"本次金额 {amount}，信用额度 {summary.credit_limit}"
            )
            if summary.mode == "block":
                raise AppException(ErrorCode.VALIDATION_ERROR, message)
            warning = message
        return CustomerCreditCheckOut(
            **summary.model_dump(exclude={"over_limit"}),
            over_limit=exceeded,
            allowed=True,
            warning=warning,
        )

    async def get_credit_summary(self, customer_id: int) -> CustomerCreditOut:
        """0 额度表示不限，可用额度返回 null，避免误报负额度和超限。"""
        customer = await self._get_active_customer(customer_id)
        receivable = (await self.customers.sum_unpaid_receivable([customer_id])).get(
            customer_id, Decimal("0")
        )
        mode = await ConfigService(self.db).get_value("sales.credit.mode", "block")
        if mode not in {"block", "warn", "off"}:
            raise AppException(ErrorCode.VALIDATION_ERROR, "信用额度策略配置无效")
        unlimited = customer.credit_limit == 0
        return CustomerCreditOut(
            credit_limit=customer.credit_limit,
            receivable_amount=receivable,
            available_credit=None if unlimited else customer.credit_limit - receivable,
            unlimited=unlimited,
            over_limit=not unlimited and receivable > customer.credit_limit,
            mode=mode,
        )

    # ----- 内部辅助 -----

    async def _get_active_customer(
        self, customer_id: int, *, for_update: bool = False
    ) -> BdCustomer:
        """按 customer_id 查未删除客户；for_update 指定是否锁行，不存在或已删除抛出 404。"""
        if for_update:
            customer = await self.customers.get_for_update(customer_id)
        else:
            customer = await self.customers.get(customer_id)
        if customer is None or customer.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "客户不存在")
        return customer

    async def _validate_unique(self, code: str, exclude_id: int | None = None) -> None:
        """校验 code 未被其他客户占用，exclude_id 可排除自己；重复抛 409。"""
        if await self.customers.get_by_code(code, exclude_id):
            raise AppException(ErrorCode.CONFLICT, f"客户编码已存在: {code}")

    @staticmethod
    def _validate_credit_limit(credit_limit: Decimal | None) -> None:
        """信用额度为负数时抛 422；None 表示本次不修改，放行。"""
        if credit_limit is None:
            return
        if credit_limit < 0:
            raise AppException(ErrorCode.VALIDATION_ERROR, "信用额度不能为负数")

    @staticmethod
    def _validate_credit_days(credit_days: int | None) -> None:
        """账期为负数时抛 422；None 表示本次不修改账期，放行。"""
        if credit_days is None:
            return
        if credit_days < 0:
            raise AppException(ErrorCode.VALIDATION_ERROR, "账期天数不能为负数")

    async def _assert_can_delete(self, customer: BdCustomer) -> None:
        """检查销售订单、出库单和应收记录；存在引用时抛 409，通过则无返回值。"""
        if await self.customers.has_sales_order(customer.id):
            raise AppException(ErrorCode.CONFLICT, "该客户存在未结销售订单，无法删除")
        if await self.customers.has_outbound(customer.id):
            raise AppException(ErrorCode.CONFLICT, "该客户存在销售出库单，无法删除")
        if await self.customers.has_receivable(customer.id):
            raise AppException(ErrorCode.CONFLICT, "该客户存在应收记录，无法删除")

    async def _next_code(self) -> str:
        """在客户写锁内读取最大序号，并用公共编码工具返回下一个 CUS 编码。"""
        max_code = await self.customers.max_code(CUSTOMER_CODE_PREFIX)
        return format_code(
            CUSTOMER_CODE_PREFIX,
            next_seq_from_code(max_code, CUSTOMER_CODE_PREFIX),
        )

    async def _to_out(self, customer: BdCustomer) -> CustomerOut:
        """把 customer 转成出参，补入应收余额。"""
        receivable_map = await self.customers.sum_unpaid_receivable([customer.id])
        return CustomerOut.model_validate(customer).model_copy(
            update={"receivable_amount": receivable_map.get(customer.id, Decimal("0"))}
        )

    async def _write_log(self, operator_id: int, action: str, target_id: int) -> None:
        """记录 operator_id 对 target_id 的 action，只写当前事务，不直接提交。"""
        params: dict[str, int] = {"customerId": target_id}
        await write_oper_log(
            self.db,
            tenant_id=self.tenant_id,
            operator_id=operator_id,
            action=action,
            params=params,
            module=OPER_MODULE,
        )
