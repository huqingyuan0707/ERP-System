"""系统参数 Schema（出参均带 camelCase 别名）。"""

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import to_camel


class ConfigOut(BaseModel):
    """参数出参。"""

    model_config = ConfigDict(
        from_attributes=True, alias_generator=to_camel, populate_by_name=True
    )

    key: str
    value: str | None = None
    name: str
    remark: str | None = None


class ConfigUpdateItem(BaseModel):
    """单条参数更新项。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    key: str = Field(min_length=1, max_length=128)
    value: str | None = Field(default=None, max_length=2000)


class ConfigBatchUpdate(BaseModel):
    """批量保存参数。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[ConfigUpdateItem] = Field(min_length=1, max_length=100)
