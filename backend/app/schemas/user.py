"""用户相关 Schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageQuery, to_camel


class UserCreate(BaseModel):
    """新增用户。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    real_name: str | None = Field(default=None, max_length=64)
    mobile: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=128)
    dept_id: int | None = None
    position: str | None = Field(default=None, max_length=64)
    status: int = 1
    role_ids: list[int] = Field(default_factory=list)


class UserUpdate(BaseModel):
    """更新用户（部分更新）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    password: str | None = Field(default=None, min_length=6, max_length=128)
    real_name: str | None = Field(default=None, max_length=64)
    mobile: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=128)
    dept_id: int | None = None
    position: str | None = Field(default=None, max_length=64)
    status: int | None = None
    role_ids: list[int] | None = None


class UserOut(BaseModel):
    """用户出参。"""

    model_config = ConfigDict(
        from_attributes=True, alias_generator=to_camel, populate_by_name=True
    )

    id: int
    username: str
    real_name: str | None = None
    mobile: str | None = None
    email: str | None = None
    dept_id: int | None = None
    position: str | None = None
    status: int = 1
    role_ids: list[int] = Field(default_factory=list)
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UserQuery(PageQuery):
    """用户列表查询。"""

    status: int | None = None
    dept_id: int | None = None


class PasswordResetIn(BaseModel):
    """重置密码请求体。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    password: str | None = Field(default=None, min_length=6, max_length=128)
