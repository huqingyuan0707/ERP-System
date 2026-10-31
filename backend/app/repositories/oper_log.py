"""操作日志数据访问（扩展：分页查询与筛选）。"""

from datetime import datetime

from sqlalchemy import select

from app.models.sys import SysOperLog
from app.repositories.base import BaseRepository


class OperLogRepository(BaseRepository[SysOperLog]):
    """操作日志仓储。"""

    model = SysOperLog

    async def list_with_filter(
        self,
        module: str | None,
        action: str | None,
        user_id: int | None,
        status: int | None,
        start_date: datetime | None,
        end_date: datetime | None,
        page: int,
        page_size: int,
    ) -> tuple[list[SysOperLog], int]:
        """按条件筛选并分页查询操作日志。"""
        stmt = select(SysOperLog).order_by(SysOperLog.created_at.desc(), SysOperLog.id.desc())
        if module:
            stmt = stmt.where(SysOperLog.module.ilike(f"%{module}%"))
        if action:
            stmt = stmt.where(SysOperLog.action.ilike(f"%{action}%"))
        if user_id is not None:
            stmt = stmt.where(SysOperLog.user_id == user_id)
        if status is not None:
            stmt = stmt.where(SysOperLog.status == status)
        if start_date is not None:
            stmt = stmt.where(SysOperLog.created_at >= start_date)
        if end_date is not None:
            stmt = stmt.where(SysOperLog.created_at <= end_date)
        return await self.paginate(stmt, page, page_size)
