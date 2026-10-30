"""字典类型/字典项 Schema（出参均带 camelCase 别名）。"""

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import to_camel


class DictTypeCreate(BaseModel):
    """新增字典类型。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=64)
    status: int = Field(default=1)
    remark: str | None = Field(default=None, max_length=255)


class DictTypeUpdate(BaseModel):
    """修改字典类型（部分更新）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    name: str | None = Field(default=None, min_length=1, max_length=64)
    status: int | None = Field(default=None)
    remark: str | None = Field(default=None, max_length=255)


class DictTypeOut(BaseModel):
    """字典类型出参。"""

    model_config = ConfigDict(
        from_attributes=True, alias_generator=to_camel, populate_by_name=True
    )

    id: int
    code: str
    name: str
    status: int
    remark: str | None = None


class DictItemCreate(BaseModel):
    """新增字典项。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    label: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=64)
    sort: int = Field(default=0)
    status: int = Field(default=1)
    remark: str | None = Field(default=None, max_length=255)


class DictItemUpdate(BaseModel):
    """修改字典项（部分更新）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    label: str | None = Field(default=None, min_length=1, max_length=64)
    value: str | None = Field(default=None, min_length=1, max_length=64)
    sort: int | None = Field(default=None)
    status: int | None = Field(default=None)
    remark: str | None = Field(default=None, max_length=255)


class DictItemOut(BaseModel):
    """字典项出参。"""

    model_config = ConfigDict(
        from_attributes=True, alias_generator=to_camel, populate_by_name=True
    )

    id: int
    type_code: str
    label: str
    value: str
    sort: int
    status: int
    remark: str | None = None
