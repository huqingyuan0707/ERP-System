"""商品相关 Schema（商品、分类、单位、导入）。"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.schemas.common import PageQuery, to_camel


class _GoodsInput(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, str_strip_whitespace=True
    )

    @field_validator("code", "barcode", mode="before", check_fields=False)
    @classmethod
    def normalize_optional_code(cls, value):
        return (value.strip() or None) if isinstance(value, str) else value

    @field_validator(
        "name",
        "sort",
        "precision",
        "cost_price",
        "sale_price",
        "stock_lower",
        "stock_upper",
        "status",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def reject_explicit_null(cls, value):
        if value is None:
            raise ValueError("该字段不能为 null")
        return value


# ---------- 商品分类 ----------


class GoodsCategoryBase(_GoodsInput):
    """商品分类公共字段。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    name: str = Field(min_length=1, max_length=64)
    parent_id: int | None = Field(default=None, gt=0)
    sort: int = Field(default=0, ge=0, le=2147483647)
    remark: str | None = Field(default=None, max_length=255)


class GoodsCategoryCreate(GoodsCategoryBase):
    """新增商品分类。"""


class GoodsCategoryUpdate(_GoodsInput):
    """修改商品分类（部分更新）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    name: str | None = Field(default=None, min_length=1, max_length=64)
    parent_id: int | None = Field(default=None, gt=0)
    sort: int | None = Field(default=None, ge=0, le=2147483647)
    remark: str | None = Field(default=None, max_length=255)


class GoodsCategoryOut(BaseModel):
    """商品分类出参，附带引用计数供前端禁用删除按钮。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    name: str
    parent_id: int | None = Field(default=None, gt=0)
    sort: int = Field(default=0, ge=0, le=2147483647)
    remark: str | None = None
    created_at: datetime | None = None
    # 子分类数量与关联商品数量：>0 时分类不可删除
    children_count: int = 0
    goods_count: int = 0


class GoodsCategoryQuery(PageQuery):
    """商品分类查询（不分页，按排序返回全部）。"""


# ---------- 计量单位 ----------


class UnitBase(_GoodsInput):
    """计量单位公共字段。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    name: str = Field(min_length=1, max_length=32)
    # 精度为数量保留的小数位，需求约定 0-4 位
    precision: int = Field(default=0, ge=0, le=4)
    remark: str | None = Field(default=None, max_length=255)


class UnitCreate(UnitBase):
    """新增计量单位。"""


class UnitUpdate(_GoodsInput):
    """修改计量单位（部分更新）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    name: str | None = Field(default=None, min_length=1, max_length=32)
    precision: int | None = Field(default=None, ge=0, le=4)
    remark: str | None = Field(default=None, max_length=255)


class UnitOut(BaseModel):
    """计量单位出参。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    name: str
    precision: int = 0
    remark: str | None = None
    created_at: datetime | None = None
    # 引用该单位的商品数量：>0 时单位不可删除
    goods_count: int = 0


class UnitQuery(PageQuery):
    """计量单位查询（不分页，按排序返回全部）。"""


# ---------- 商品 ----------


class GoodsBase(_GoodsInput):
    """商品公共字段。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    name: str = Field(min_length=1, max_length=128)
    category_id: int | None = Field(default=None, gt=0)
    spec: str | None = Field(default=None, max_length=128)
    unit_id: int | None = Field(default=None, gt=0)
    barcode: str | None = Field(default=None, max_length=64)
    cost_price: Decimal = Field(
        default=Decimal("0"), ge=0, max_digits=18, decimal_places=2, allow_inf_nan=False
    )
    sale_price: Decimal = Field(
        default=Decimal("0"), ge=0, max_digits=18, decimal_places=2, allow_inf_nan=False
    )
    stock_lower: Decimal = Field(
        default=Decimal("0"), ge=0, max_digits=18, decimal_places=4, allow_inf_nan=False
    )
    stock_upper: Decimal = Field(
        default=Decimal("0"), ge=0, max_digits=18, decimal_places=4, allow_inf_nan=False
    )
    status: Literal[0, 1] = 1
    remark: str | None = Field(default=None, max_length=255)


class GoodsCreate(GoodsBase):
    """新增商品，编码留空时按前缀自动生成。"""

    code: str | None = Field(default=None, max_length=32)


class GoodsUpdate(_GoodsInput):
    """更新商品（部分更新）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    name: str | None = Field(default=None, min_length=1, max_length=128)
    category_id: int | None = Field(default=None, gt=0)
    spec: str | None = Field(default=None, max_length=128)
    unit_id: int | None = Field(default=None, gt=0)
    barcode: str | None = Field(default=None, max_length=64)
    cost_price: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2, allow_inf_nan=False
    )
    sale_price: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2, allow_inf_nan=False
    )
    stock_lower: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=4, allow_inf_nan=False
    )
    stock_upper: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=4, allow_inf_nan=False
    )
    status: Literal[0, 1] | None = None
    remark: str | None = Field(default=None, max_length=255)


class GoodsOut(BaseModel):
    """商品出参（含分类/单位名称）。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    code: str
    name: str
    category_id: int | None = Field(default=None, gt=0)
    category_name: str | None = None
    spec: str | None = None
    unit_id: int | None = Field(default=None, gt=0)
    unit_name: str | None = None
    barcode: str | None = None
    cost_price: Decimal = Decimal("0")
    sale_price: Decimal = Decimal("0")
    stock_lower: Decimal = Decimal("0")
    stock_upper: Decimal = Decimal("0")
    status: Literal[0, 1] = 1
    remark: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_serializer("cost_price", "sale_price", "stock_lower", "stock_upper", when_used="json")
    def serialize_decimal(self, value: Decimal) -> float:
        """仅响应层保持历史 JSON 数字契约，业务校验仍使用 Decimal。"""
        return float(value)


class GoodsQuery(PageQuery):
    """商品列表查询。"""

    category_id: int | None = Field(default=None, gt=0)
    status: Literal[0, 1] | None = None


class GoodsImportErrorItem(BaseModel):
    """导入错误行：row 为 Excel 行号，errors 为该行全部校验失败原因。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    row: int
    errors: list[str] = Field(default_factory=list)


class GoodsImportResult(BaseModel):
    """商品导入结果：全部校验通过才入库，任一错误行则 success=False 且 0 行入库。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    success: bool = False
    total: int = 0
    success_count: int = 0
    fail_count: int = 0
    errors: list[GoodsImportErrorItem] = Field(default_factory=list)
