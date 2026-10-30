"""角色 Schema（出参均带 camelCase 别名）。"""

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import to_camel


class RoleCreate(BaseModel):
    """新增角色。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=64)
    dataScope: int = Field(default=1, ge=1, le=3)
    remark: str | None = Field(default=None, max_length=255)


class RoleUpdate(BaseModel):
    """修改角色（部分更新）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    name: str | None = Field(default=None, min_length=1, max_length=64)
    remark: str | None = Field(default=None, max_length=255)


class RoleOut(BaseModel):
    """角色出参。"""

    model_config = ConfigDict(
        from_attributes=True, alias_generator=to_camel, populate_by_name=True
    )

    id: int
    code: str
    name: str
    data_scope: int
    status: int
    remark: str | None = None
    dept_ids: list[int] = []


class DataScopeAssign(BaseModel):
    """数据范围分配。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    dataScope: int = Field(ge=1, le=3)
    deptIds: list[int] = Field(default_factory=list, max_length=100)
