"""采购申请 Schema（P-01）：字段按《数据库表设计.md》6.1/6.2 节 pur_apply / pur_apply_item。

状态口径（与 app/models/pur.py 保持一致，禁止裸数字散落业务代码）：
  - 0 草稿：可编辑、可删除、可提交
  - 1 待审：不可编辑、不可删除，可审核（通过 / 驳回）
  - 2 已审：仅作采购参考，后续由采购员据此建采购订单（V1 不自动转单）

对外字段一律 camelCase（alias_generator=to_camel），内部 Python 侧 snake_case。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import PageQuery, to_camel


class PurApplyStatus:
    """采购申请状态常量（与 pur_apply.status 一一对应，禁止业务代码写裸数字）。

    用类常量而不是模块常量：既能在类体内被引用（模块级常量定义在类之后会被判为未定义），
    也不会与 ruff isort 的「导入块后空行数」规则冲突。
    """

    DRAFT = 0  # 草稿：可编辑、可删除、可提交
    PENDING = 1  # 待审：不可编辑，等待审核
    APPROVED = 2  # 已审：仅作采购参考，由采购员据此建采购订单（V1 不自动转单）


class PurApplyItemIn(BaseModel):
    """申请明细行入参：只允许选商品、填数量与需求日期，金额/单价不在采购申请范围内。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    # 商品 ID 必填；是否存在、是否启用由 Service 统一校验（避免每个明细行查一次库）
    goods_id: int
    # 申请数量必须大于 0，且按 NUMERIC(18,4) 精度录入；<=0 直接 422 拦截
    qty: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    # 行级需求日期：可空表示沿用主表需求日期；填写时用于到货排期参考
    need_date: date | None = None


class PurApplyCreate(BaseModel):
    """新增采购申请（保存即草稿）；单号由后端生成，申请人默认当前登录用户。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    # 申请部门：不传时后端默认取当前用户所属部门（sys_user.dept_id）
    dept_id: int | None = None
    # 申请人：不传时后端默认取当前登录用户 ID（pur_apply.applicant_id 是 sys_user.id 外键）
    applicant_id: int | None = None
    # 需求日期：新建时必填（验收要求「需求日期必填」），到货期限参考
    need_date: date
    # 备注：申请原因、用途说明等，最长 255 与表结构对齐
    remark: str | None = Field(default=None, max_length=255)
    # 明细：至少 1 行，最多 200 行（上限与 Service 侧常量一致，防止一次提交过大报文）
    items: list[PurApplyItemIn] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def reject_duplicate_goods(self) -> "PurApplyCreate":
        """同一张申请不允许重复录入同一商品，否则统计与后续建单会重复采购。"""
        goods_ids = [item.goods_id for item in self.items]
        if len(goods_ids) != len(set(goods_ids)):
            raise ValueError("同一商品不能重复录入多行，请合并数量")
        return self


class PurApplyUpdate(BaseModel):
    """修改采购申请草稿：整单覆盖式更新（主表字段 + 明细行全量替换）。

    只有草稿（status=0）允许调用；待审/已审单据修改一律 409，
    需要改动时先由审核人驳回回到草稿。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    # 申请部门：不传保持原值；部门变更会同步影响数据范围过滤口径
    dept_id: int | None = None
    # 需求日期：不传保持原值
    need_date: date | None = None
    # 备注：显式传 null 表示清空备注
    remark: str | None = Field(default=None, max_length=255)
    # 明细：不传表示只改主表字段；传了就整单替换（旧行软删，保留流水便于追溯），最多 200 行
    items: list[PurApplyItemIn] | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def reject_duplicate_goods(self) -> "PurApplyUpdate":
        """明细整体替换时同样校验商品不重复。"""
        if self.items is None:
            return self
        goods_ids = [item.goods_id for item in self.items]
        if len(goods_ids) != len(set(goods_ids)):
            raise ValueError("同一商品不能重复录入多行，请合并数量")
        return self


class PurApplyItemOut(BaseModel):
    """申请明细出参；商品编码/名称/规格/单位由 Service 批量补齐，避免前端逐行查商品。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    goods_id: int
    goods_code: str | None = None  # 商品编码，用于打印与列表展示
    goods_name: str | None = None  # 商品名称
    spec: str | None = None  # 商品规格
    unit_name: str | None = None  # 计量单位名称
    qty: Decimal = Decimal("0")  # 申请数量
    need_date: date | None = None  # 行级需求日期


class PurApplyOut(BaseModel):
    """申请单出参（主表 + 明细）；列表接口只返回主表字段与 partsCount，明细为空表示未加载。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    apply_no: str  # 申请单号，PA+yyyyMMdd+4 位序号
    dept_id: int
    dept_name: str | None = None  # 申请部门名称
    applicant_id: int
    applicant_name: str | None = None  # 申请人姓名（无真实姓名时回退登录账号）
    need_date: date | None = None  # 主表需求日期
    status: int = PurApplyStatus.DRAFT  # 0 草稿 / 1 待审 / 2 已审
    audit_by: int | None = None  # 审核人用户 ID
    audit_by_name: str | None = None  # 审核人姓名
    audit_at: date | None = None  # 审核日期（模型字段为 DATE，仅到天）
    audit_remark: str | None = None  # 驳回原因
    remark: str | None = None  # 备注
    version: int = 1  # 乐观锁版本号，前端提交修改时回传
    items_count: int = 0  # 明细行数，列表页直接用，免去加载明细
    created_at: datetime | None = None
    items: list[PurApplyItemOut] = Field(default_factory=list)  # 明细行（详情/打印使用）


class PurApplyQuery(PageQuery):
    """申请单列表查询：支持状态、部门、需求日期区间与关键字（单号/部门/申请人）。"""

    # None 表示不按状态筛选；0/1/2 分别对应草稿/待审/已审
    status: int | None = Field(default=None, ge=PurApplyStatus.DRAFT, le=PurApplyStatus.APPROVED)
    dept_id: int | None = None  # 按申请部门筛选
    # 需求日期区间（含边界），前端传 YYYY-MM-DD
    need_date_start: date | None = None
    need_date_end: date | None = None


class PurApplyAudit(BaseModel):
    """审核动作入参：result=approved 通过；result=rejected 驳回且必须填写原因。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    result: Literal["approved", "rejected"]  # 审核结论，只允许这两个值
    # 审核意见：驳回时必填（前端与后端都校验），通过时可选，最长 255 与表结构对齐
    remark: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def require_remark_when_rejected(self) -> "PurApplyAudit":
        """驳回必须给出原因，便于申请人修改后重新提交，保证可追溯。"""
        if self.result == AUDIT_RESULT_REJECTED and not (self.remark or "").strip():
            raise ValueError("驳回采购申请必须填写驳回原因")
        return self


# 审核动作：approved=通过（待审→已审）；rejected=驳回（待审→草稿并记录驳回原因）
AUDIT_RESULT_APPROVED = "approved"
AUDIT_RESULT_REJECTED = "rejected"

# 驳回原因长度上限：与 pur_apply.audit_remark 的 VARCHAR(255) 对齐
AUDIT_REMARK_MAX_LENGTH = 255
# 单张申请明细行数上限：防止一次提交过大报文拖垮事务
APPLY_ITEM_MAX_COUNT = 200

# 状态别名：语义化引用（等同于 PurApplyStatus.DRAFT / PENDING / APPROVED）
APPLY_STATUS_DRAFT = PurApplyStatus.DRAFT
APPLY_STATUS_PENDING = PurApplyStatus.PENDING
APPLY_STATUS_APPROVED = PurApplyStatus.APPROVED
