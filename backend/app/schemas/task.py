"""异步任务 Schema（C-07 Excel 导入导出与任务）。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageQuery, to_camel
from app.schemas.inventory import FlowQuery, StockQuery


class StockExportQuery(StockQuery):
    """库存导出筛选参数（GET /inventory/stocks/export）。

    继承库存列表查询，保证导出与列表使用同一套筛选条件；
    page/pageSize 对导出无意义，仅用于兼容 Query 模型。
    """


class FlowExportQuery(FlowQuery):
    """流水导出筛选参数（GET /inventory/flows/export）。

    继承流水列表查询，保证导出与列表使用同一套筛选条件。
    """


class ReportExportQuery(BaseModel):
    """通用报表导出参数（POST /reports/export）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    report_type: str = Field(
        min_length=1,
        max_length=32,
        description="报表类型 purchase/sales/inventory/finance",
    )


class TaskQuery(PageQuery):
    """异步任务列表查询参数。"""

    task_type: str | None = None
    status: int | None = Field(default=None, ge=1, le=4)


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
    can_download: bool = False
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
