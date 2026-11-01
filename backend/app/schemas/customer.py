"""客户相关 Schema：定义新增、部分更新、出参与查询参数，字段以 camelCase 对外。"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageQuery, to_camel


class CustomerCreate(BaseModel):
    """新增客户；编码生成与唯一性校验由 Service 层负责。"""

    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )

    code: str | None = Field(default=None, max_length=32)  # 编码可空，由业务层统一生成
    name: str = Field(min_length=1, max_length=128)  # 必填
    contact: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    tax_no: str | None = Field(default=None, max_length=64)
    bank: str | None = Field(default=None, max_length=128)
    account: str | None = Field(default=None, max_length=64)
    # 信用额度不可为负，负数直接由 Schema 拦截为 422
    credit_limit: Decimal = Field(default=0, ge=0)
    # 账期用于推算应收到期日，负数无业务意义，Schema 拦截为 422
    credit_days: int = Field(default=0, ge=0)
    status: int = Field(default=1, ge=0, le=1)
    remark: str | None = Field(default=None, max_length=255)


class CustomerUpdate(BaseModel):
    """部分更新客户；通过 model_dump(exclude_unset=True) 区分未传字段与显式传空。"""

    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )

    code: str | None = Field(default=None, max_length=32)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    contact: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    tax_no: str | None = Field(default=None, max_length=64)
    bank: str | None = Field(default=None, max_length=128)
    account: str | None = Field(default=None, max_length=64)
    credit_limit: Decimal | None = Field(default=None, ge=0)
    credit_days: int | None = Field(default=None, ge=0)
    status: int | None = Field(default=None, ge=0, le=1)
    remark: str | None = Field(default=None, max_length=255)


class CustomerOut(BaseModel):
    """客户出参；附带应收余额，供列表展示该客户的未核销应收款。"""

    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )

    id: int
    code: str
    name: str
    contact: str | None = None
    phone: str | None = None
    tax_no: str | None = None
    bank: str | None = None
    account: str | None = None
    credit_limit: Decimal = Decimal("0")
    credit_days: int = 0
    status: int = 1
    # 应收余额不是客户表字段，由业务层按应收单据汇总后补入
    receivable_amount: Decimal = Decimal("0")
    remark: str | None = None
    created_at: datetime | None = None


class CustomerCreditOut(BaseModel):
    """客户信用概况，供前端「信用额度超限提示」与销售订单审核使用。"""

    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )

    credit_limit: Decimal = Decimal("0")
    receivable_amount: Decimal = Decimal("0")
    available_credit: Decimal = Decimal("0")
    over_limit: bool = False


class CustomerQuery(PageQuery):
    """客户列表查询；复用分页与关键字参数，额外支持状态筛选。"""

    # None 不筛选；0 查停用，1 查启用
    status: int | None = Field(default=None, ge=0, le=1)
