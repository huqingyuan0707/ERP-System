"""员工 Schema（B-06）：字段按《数据库表设计.md》5.7 节。"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from app.schemas.common import PageQuery, to_camel


class EmployeeCreate(BaseModel):
    """新增员工；编码留空由后端自动生成，userId 为关联的系统账号。"""

    model_config = ConfigDict(
        from_attributes=True, alias_generator=to_camel, populate_by_name=True
    )

    code: str | None = Field(default=None, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    dept_id: int
    position: str | None = Field(default=None, max_length=64)
    mobile: str | None = Field(default=None, max_length=32)
    entry_date: date | None = None
    status: int = Field(default=1, ge=0, le=1)
    user_id: int | None = None
    remark: str | None = Field(default=None, max_length=255)


class EmployeeUpdate(BaseModel):
    """部分更新员工；显式 null 可清空可空列，非空列不允许传 null。"""

    model_config = ConfigDict(
        from_attributes=True, alias_generator=to_camel, populate_by_name=True
    )

    code: str | None = Field(default=None, max_length=32)
    name: str | None = Field(default=None, min_length=1, max_length=64)
    dept_id: int | None = None
    position: str | None = Field(default=None, max_length=64)
    mobile: str | None = Field(default=None, max_length=32)
    entry_date: date | None = None
    status: int | None = Field(default=None, ge=0, le=1)
    user_id: int | None = None
    remark: str | None = Field(default=None, max_length=255)

    @field_validator("code", "name", "dept_id", "status", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object, info: ValidationInfo) -> object:
        if value is None:
            raise ValueError(f"{info.field_name} 不能为 null")
        return value


class EmployeeOut(BaseModel):
    """员工出参；部门名称与登录账号由业务层补齐。"""

    model_config = ConfigDict(
        from_attributes=True, alias_generator=to_camel, populate_by_name=True
    )

    id: int
    code: str
    name: str
    dept_id: int
    dept_name: str | None = None
    position: str | None = None
    mobile: str | None = None
    entry_date: date | None = None
    status: int = 1
    user_id: int | None = None
    username: str | None = None
    remark: str | None = None
    created_at: datetime | None = None


class EmployeeQuery(PageQuery):
    """员工列表查询；includeSubDept 为真时按部门子树筛选。"""

    status: int | None = Field(default=None, ge=0, le=1)
    dept_id: int | None = None
    include_sub_dept: bool = False


class UserOptionOut(BaseModel):
    """可关联的系统用户；linkedEmployeeId 非空表示已被其他员工占用。"""

    model_config = ConfigDict(
        from_attributes=True, alias_generator=to_camel, populate_by_name=True
    )

    id: int
    username: str
    real_name: str | None = None
    dept_id: int | None = None
    status: int = 1
    linked_employee_id: int | None = None
