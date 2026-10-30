"""登录日志数据访问。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.models.sys import SysLoginLog
from app.repositories.base import BaseRepository


class LoginLogRepository(BaseRepository[SysLoginLog]):
    """登录日志仓储。"""

    model = SysLoginLog

    async def create(
        self,
        username: str,
        ip: str | None,
        user_agent: str | None,
        status: int,
        message: str,
    ) -> SysLoginLog:
        """写入一条登录日志。"""
        return await self.add(
            SysLoginLog(
                username=username,
                ip=ip,
                user_agent=user_agent,
                status=status,
                message=message,
            )
        )

    async def count_failures_since(self, username: str, ip: str | None, seconds: int) -> int:
        """统计指定 username（可选限定 ip）最近 seconds 秒内的失败记录数。"""
        threshold = datetime.now(UTC) - timedelta(seconds=seconds)
        stmt = (
            select(func.count())
            .select_from(SysLoginLog)
            .where(
                SysLoginLog.username == username,
                SysLoginLog.status == 0,
                SysLoginLog.created_at >= threshold,
            )
        )
        if ip:
            stmt = stmt.where(SysLoginLog.ip == ip)
        return int((await self.db.execute(stmt)).scalar_one())

    async def list_recent(self, username: str, limit: int) -> list[SysLoginLog]:
        """按时间倒序取指定 username 最近 limit 条登录记录。"""
        stmt = (
            select(SysLoginLog)
            .where(SysLoginLog.username == username)
            .order_by(SysLoginLog.created_at.desc(), SysLoginLog.id.desc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars().all())
