"""登录日志业务逻辑（C-06）。"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.login_log import LoginLogRepository
from app.schemas.common import PageResult
from app.schemas.login_log import LoginLogOut, LoginLogQuery
from app.utils.pagination import paginate_result


class LoginLogService:
    """登录日志查询业务（只读，不提供删除）。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.login_logs = LoginLogRepository(db)

    async def list_login_logs(self, query: LoginLogQuery) -> PageResult[LoginLogOut]:
        """按条件分页查询登录日志。"""
        start_date = (
            datetime.strptime(query.start_date, "%Y-%m-%d") if query.start_date else None
        )
        end_date = (
            datetime.strptime(query.end_date, "%Y-%m-%d") if query.end_date else None
        )
        rows, total = await self.login_logs.list_with_filter(
            username=query.username,
            status=query.status,
            start_date=start_date,
            end_date=end_date,
            page=query.page,
            page_size=query.page_size,
        )
        items = [LoginLogOut.model_validate(row) for row in rows]
        return paginate_result(items, total, query.page, query.page_size)
