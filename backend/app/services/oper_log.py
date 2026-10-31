"""操作日志业务逻辑（C-06）。"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.oper_log import OperLogRepository
from app.schemas.common import PageResult
from app.schemas.oper_log import OperLogOut, OperLogQuery
from app.utils.pagination import paginate_result


class OperLogService:
    """操作日志查询业务（只读，不提供删除）。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.oper_logs = OperLogRepository(db)

    async def list_oper_logs(self, query: OperLogQuery) -> PageResult[OperLogOut]:
        """按条件分页查询操作日志。"""
        start_date = (
            datetime.strptime(query.start_date, "%Y-%m-%d") if query.start_date else None
        )
        end_date = (
            datetime.strptime(query.end_date, "%Y-%m-%d") if query.end_date else None
        )
        rows, total = await self.oper_logs.list_with_filter(
            module=query.module,
            action=query.action,
            user_id=query.user_id,
            status=query.status,
            start_date=start_date,
            end_date=end_date,
            page=query.page,
            page_size=query.page_size,
        )
        items = [OperLogOut.model_validate(row) for row in rows]
        return paginate_result(items, total, query.page, query.page_size)
