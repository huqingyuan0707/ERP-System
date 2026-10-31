"""供应商相关 Schema：定义新增、部分更新、出参和查询参数，字段以 camelCase 对外。"""

from datetime import datetime  # 日期时间类型，用来声明供应商的创建时间。
from decimal import Decimal  # 定点数类型，应付余额用金额类型避免浮点误差。

from pydantic import BaseModel, ConfigDict, Field  # 模型基类、模型配置和字段校验规则。

from app.schemas.common import PageQuery, to_camel  # 复用分页参数和驼峰别名转换函数。


class SupplierCreate(BaseModel):  # 继承 BaseModel，让 Pydantic 按字段声明解析和校验新增请求。
    """新增供应商；编码生成与唯一性校验由 Service 层负责。"""

    model_config = ConfigDict(  # model_config 是 Pydantic 识别的配置名，配置作用于整个模型。
        from_attributes=True,  # 除字典外，也允许 model_validate 从对象属性读取字段。
        alias_generator=to_camel,  # 自动生成 taxNo、creditDays 等别名；按别名序列化时使用这些名称。
        populate_by_name=True,  # 有别名时仍接受 tax_no 等 Python 字段名，方便后端调用。
    )  # 这些配置只控制数据解析与别名，不会建表或保存数据。

    # 编码允许省略或留空，由业务层统一生成，不在 Schema 中访问数据库。
    code: str | None = Field(default=None, max_length=32)  # 编码可空，未传为 None；最长 32 字符。
    name: str = Field(min_length=1, max_length=128)  # 未设置默认值，因此必填；长度为 1～128 字符。
    contact: str | None = Field(default=None, max_length=64)  # 联系人可空，最长 64 字符。
    phone: str | None = Field(default=None, max_length=32)  # 联系电话可空，最长 32 字符。
    address: str | None = Field(default=None, max_length=255)  # 地址可空，最长 255 字符。
    tax_no: str | None = Field(default=None, max_length=64)  # 税号可空，最长 64 字符。
    bank: str | None = Field(default=None, max_length=128)  # 开户行可空，最长 128 字符。
    account: str | None = Field(default=None, max_length=64)  # 银行账号可空，最长 64 字符。
    # 账期用于推算应付到期日，负数没有业务意义，直接由 Schema 拦截为 422。
    credit_days: int = Field(default=0, ge=0)  # 默认 0 天（款到发货）；ge 表示大于等于 0。
    # 状态只允许 0（停用）或 1（启用），避免无效状态进入业务层。
    status: int = Field(default=1, ge=0, le=1)  # 默认启用；ge 表示大于等于，le 表示小于等于。
    remark: str | None = Field(default=None, max_length=255)  # 备注可不传；最多 255 字符。


class SupplierUpdate(BaseModel):  # 单独声明更新模型，避免继承新增模型中必填的 name。
    """部分更新供应商；通过 model_dump(exclude_unset=True) 区分未传字段与显式传空。"""

    model_config = ConfigDict(  # 更新请求沿用新增请求的数据解析和别名规则。
        from_attributes=True,  # 允许从对象属性构造更新模型，不局限于字典输入。
        alias_generator=to_camel,  # 前端可以使用 taxNo、creditDays 等 camelCase 字段名。
        populate_by_name=True,  # 后端也可以用 tax_no、credit_days 等 snake_case 字段名。
    )  # 只定义解析配置；哪些字段真正写入数据库由后续 Service 决定。

    # 各字段均可省略；exclude_unset=True 会保留显式传入的 None、False 和 0。
    code: str | None = Field(default=None, max_length=32)  # 编码可省略；传字符串时仍限制 32 字符。
    name: str | None = Field(default=None, min_length=1, max_length=128)  # 可省略；字符串不能为空。
    contact: str | None = Field(default=None, max_length=64)  # 可省略或传 None 表示清空联系人。
    phone: str | None = Field(default=None, max_length=32)  # 可省略或传 None 表示清空电话。
    address: str | None = Field(default=None, max_length=255)  # 可请求清空地址，长度上限不变。
    tax_no: str | None = Field(default=None, max_length=64)  # 可请求清空税号。
    bank: str | None = Field(default=None, max_length=128)  # 可请求清空开户行。
    account: str | None = Field(default=None, max_length=64)  # 可请求清空银行账号。
    credit_days: int | None = Field(default=None, ge=0)  # 账期不能为负；未传表示不修改账期。
    status: int | None = Field(default=None, ge=0, le=1)  # 不默认启用，避免意外修改原状态。
    remark: str | None = Field(default=None, max_length=255)  # 未传保留备注，传 None 可请求清空。


class SupplierOut(BaseModel):  # 定义对外返回的数据结构，不直接把整条数据库记录返回给前端。
    """供应商出参；附带应付余额，供列表展示该供应商的未核销应付款。"""

    model_config = ConfigDict(  # 配置输出模型的数据来源和字段别名。
        from_attributes=True,  # 让 model_validate(供应商对象) 可以读取其 id、code 等 ORM 属性。
        alias_generator=to_camel,  # 按别名序列化时，将 tax_no、credit_days 转为驼峰名称。
        populate_by_name=True,  # 接受 ORM 属性或后端字典中的原始 snake_case 字段名。
    )  # 序列化时仍需由调用方启用别名，例如 model_dump(by_alias=True)。

    id: int  # 必须提供供应商主键，前端依靠它发起详情、编辑或删除请求。
    code: str  # 必须提供已确定的供应商编码，不能像新增请求那样省略。
    name: str  # 必须提供供应商名称，用于列表和详情展示。
    contact: str | None = None  # 联系人；未维护时为 None。
    phone: str | None = None  # 联系电话；未维护时为 None。
    address: str | None = None  # 地址；未维护时为 None。
    tax_no: str | None = None  # 税号；未维护时为 None。
    bank: str | None = None  # 开户行；未维护时为 None。
    account: str | None = None  # 银行账号；未维护时为 None。
    credit_days: int = 0  # 账期天数，用于推算应付到期日。
    status: int = 1  # 供应商状态；1 启用、0 停用。
    # 应付余额不是供应商表字段，需要由业务层按应付单据汇总后补入。
    payable_amount: Decimal = Decimal("0")  # 未核销应付合计；应付模块未启用时为 0。
    remark: str | None = None  # 备注；未填写时为 None。
    created_at: datetime | None = None  # JSON 模式输出时间字符串；不会自动填入当前时间。


class SupplierQuery(PageQuery):  # 继承 page、page_size、keyword，以及父类的 camelCase 别名配置。
    """供应商列表查询；复用分页与关键字参数，额外支持状态筛选。"""

    # 未传状态时不过滤；0 是有效的停用筛选值，不能按真假值判断是否传入。
    status: int | None = Field(default=None, ge=0, le=1)  # None 不筛选，0 查停用，1 查启用。
