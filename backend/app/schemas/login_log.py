"""登录日志 Schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageQuery, to_camel


class LoginLogQuery(PageQuery):
    """登录日志列表查询参数。

    支持按用户名、状态、时间区间筛选。
    """

    username: str | None = None
    status: int | None = None
    start_date: str | None = Field(default=None, description="开始日期 YYYY-MM-DD")
    end_date: str | None = Field(default=None, description="结束日期 YYYY-MM-DD")


class LoginLogOut(BaseModel):
    """登录日志返回模型。"""

    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )

    id: int
    username: str
    ip: str | None = None
    user_agent: str | None = None
    status: int = 1
    message: str | None = None
    created_at: datetime | None = None
