"""认证相关 Schema。"""

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import to_camel


class LoginRequest(BaseModel):
    """登录请求体。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    """刷新令牌请求体（refresh token 优先从 cookie 读取，body 为可选兜底）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    refresh_token: str | None = None


class LogoutRequest(BaseModel):
    """登出请求体（refresh token 可选，用于一并撤销）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    refresh_token: str | None = None


class PasswordChangeRequest(BaseModel):
    """修改密码请求体。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class TokenOut(BaseModel):
    """登录成功返回的令牌信息。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class MenuOut(BaseModel):
    """前端菜单树节点。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: int
    name: str
    route_path: str | None = None
    icon: str | None = None
    children: list["MenuOut"] = Field(default_factory=list)


class CurrentUserOut(BaseModel):
    """当前登录用户信息、角色、权限码与菜单树。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: int
    username: str
    real_name: str | None = None
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    menus: list[MenuOut] = Field(default_factory=list)
