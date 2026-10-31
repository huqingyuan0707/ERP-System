"""异步任务 Schema（C-07 Excel 导入导出与任务）。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageQuery, to_camel


class TaskQuery(PageQuery):
    """异步任务列表查询参数。"""

    task_type: str | None = None
    status: int | None = None


class TaskOut(BaseModel):
    """异步任务返回模型，供前端轮询进度与下载。"""

    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )

    id: int
    task_id: str
    task_type: str
    status: int  # 1=待执行, 2=执行中, 3=成功, 4=失败
    params: str | None = None
    file_path: str | None = None
    error_msg: str | None = None
    progress: int = 0
    created_by: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None


class TaskCreate(BaseModel):
    """新建异步任务请求体。"""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    task_type: str = Field(min_length=1, max_length=32)
    params: str | None = None
    created_by: int | None = None
