"""操作日志 Schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageQuery, to_camel


class OperLogQuery(PageQuery):
    """操作日志列表查询参数。

    支持按用户、模块、动作、状态、时间区间筛选。
    """

    module: str | None = None
    action: str | None = None
    user_id: int | None = None
    status: int | None = None
    start_date: str | None = Field(default=None, description="开始日期 YYYY-MM-DD")
    end_date: str | None = Field(default=None, description="结束日期 YYYY-MM-DD")


class OperLogOut(BaseModel):
    """操作日志返回模型。"""

    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )

    id: int
    user_id: int | None = None
    user_name: str | None = None
    module: str | None = None
    action: str | None = None
    method: str | None = None
    url: str | None = None
    ip: str | None = None
    params: str | None = None
    result: str | None = None
    duration_ms: int | None = None
    status: int = 1
    created_at: datetime | None = None
