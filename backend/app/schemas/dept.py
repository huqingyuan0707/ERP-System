"""部门 Schema（B-06）：树形列表出参、新增、部分更新与批量排序。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from app.schemas.common import to_camel


class DeptCreate(BaseModel):
    """新增部门；parent_id 为空表示顶级部门。"""

    model_config = ConfigDict(
        from_attributes=True, alias_generator=to_camel, populate_by_name=True
    )

    name: str = Field(min_length=1, max_length=64)
    parent_id: int | None = None
    leader: str | None = Field(default=None, max_length=64)
    sort: int = Field(default=0, ge=0, le=9999)
    status: int = Field(default=1, ge=0, le=1)
    remark: str | None = Field(default=None, max_length=255)


class DeptUpdate(BaseModel):
    """部分更新部门；未传字段保持原值，parent_id 允许显式 null 移到顶层。"""

    model_config = ConfigDict(
        from_attributes=True, alias_generator=to_camel, populate_by_name=True
    )

    name: str | None = Field(default=None, min_length=1, max_length=64)
    parent_id: int | None = None
    leader: str | None = Field(default=None, max_length=64)
    sort: int | None = Field(default=None, ge=0, le=9999)
    status: int | None = Field(default=None, ge=0, le=1)
    remark: str | None = Field(default=None, max_length=255)

    @field_validator("name", "sort", "status", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object, info: ValidationInfo) -> object:
        """非空列不允许显式传 null；省略字段仍由 exclude_unset 保持原值。"""
        if value is None:
            raise ValueError(f"{info.field_name} 不能为 null")
        return value


class DeptOut(BaseModel):
    """部门出参；附带引用计数，便于前端预先禁用删除按钮。"""

    model_config = ConfigDict(
        from_attributes=True, alias_generator=to_camel, populate_by_name=True
    )

    id: int
    name: str
    parent_id: int | None = None
    leader: str | None = None
    sort: int = 0
    status: int = 1
    remark: str | None = None
    children_count: int = 0
    employee_count: int = 0
    user_count: int = 0
    created_at: datetime | None = None


class DeptSortItem(BaseModel):
    """单个部门的排序值。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: int
    sort: int = Field(ge=0, le=9999)


class DeptSortPayload(BaseModel):
    """批量排序请求，整体在一个事务内生效。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[DeptSortItem] = Field(min_length=1, max_length=200)
