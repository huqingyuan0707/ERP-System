"""部门 Schema（出参带 camelCase 别名）。"""

from pydantic import BaseModel, ConfigDict

from app.schemas.common import to_camel


class DeptOut(BaseModel):
    """部门出参。"""

    model_config = ConfigDict(
        from_attributes=True, alias_generator=to_camel, populate_by_name=True
    )

    id: int
    name: str
    parent_id: int | None = None
    sort: int
    status: int
