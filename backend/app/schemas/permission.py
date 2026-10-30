"""权限与角色分配相关 Schema。"""

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import to_camel


class PermissionNodeOut(BaseModel):
    """权限树节点（目录/菜单/按钮）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: int
    parent_id: int | None = None
    name: str
    type: int
    perm_code: str | None = None
    route_path: str | None = None
    component: str | None = None
    icon: str | None = None
    sort: int = 0
    children: list["PermissionNodeOut"] = Field(default_factory=list)


class RolePermissionAssign(BaseModel):
    """角色权限分配请求体（全量覆盖）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    permission_ids: list[int] = Field(default_factory=list)
