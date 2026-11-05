"""系统维护服务：定期清理过期数据。"""

from datetime import UTC, datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.idempotency import IdempotencyRepository
from app.repositories.oper_log import OperLogRepository


class MaintenanceService:
    """系统维护服务：提供定期清理过期数据的能力。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._idempotency_repo = IdempotencyRepository(db)
        self._oper_log_repo = OperLogRepository(db)

    async def cleanup_expired_idempotency_keys(self) -> dict[str, int]:
        """清理过期的幂等键记录。

        返回清理统计信息：
        {
            "deleted_count": 删除的记录数,
            "timestamp": 清理时间戳
        }

        建议在定时任务中调用此方法，例如每天凌晨执行一次。
        """
        deleted_count = await self._idempotency_repo.delete_expired()
        await self.db.commit()

        return {
            "deleted_count": deleted_count,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    async def cleanup_old_oper_logs(self, retention_days: int = 90) -> dict[str, int]:
        """清理超过保留期限的操作日志。

        参数：
            retention_days: 保留天数，默认90天

        返回清理统计信息：
        {
            "deleted_count": 删除的记录数,
            "retention_days": 保留天数,
            "cutoff_date": 截止日期,
            "timestamp": 清理时间戳
        }

        建议在定时任务中调用此方法，例如每周执行一次。
        """
        cutoff_date = datetime.now(UTC) - timedelta(days=retention_days)
        deleted_count = await self._oper_log_repo.delete_before_date(cutoff_date)
        await self.db.commit()

        return {
            "deleted_count": deleted_count,
            "retention_days": retention_days,
            "cutoff_date": cutoff_date.isoformat(),
            "timestamp": datetime.now(UTC).isoformat(),
        }

    async def get_idempotency_stats(self) -> dict[str, int]:
        """获取幂等键表的统计信息，用于监控。

        返回：
        {
            "total_count": 总记录数,
            "expired_count": 已过期记录数,
            "active_count": 未过期记录数
        }
        """
        from app.models.idempotency import IdempotencyKey
        from sqlalchemy import select, func

        now = datetime.now(UTC)

        # 总记录数
        total_stmt = select(func.count()).select_from(IdempotencyKey)
        total = int((await self.db.execute(total_stmt)).scalar_one())

        # 已过期记录数
        expired_stmt = select(func.count()).select_from(
            select(IdempotencyKey).where(IdempotencyKey.expires_at <= now).subquery()
        )
        expired = int((await self.db.execute(expired_stmt)).scalar_one())

        # 未过期记录数
        active = total - expired

        return {
            "total_count": total,
            "expired_count": expired,
            "active_count": active,
        }

    async def get_oper_log_stats(self) -> dict[str, int]:
        """获取操作日志表的统计信息，用于监控。

        返回：
        {
            "total_count": 总记录数,
            "last_30_days": 最近30天记录数,
            "last_90_days": 最近90天记录数
        }
        """
        from app.models.sys import SysOperLog
        from sqlalchemy import select, func

        now = datetime.now(UTC)

        # 总记录数
        total_stmt = select(func.count()).select_from(SysOperLog)
        total = int((await self.db.execute(total_stmt)).scalar_one())

        # 最近30天记录数
        thirty_days_ago = now - timedelta(days=30)
        recent_30_stmt = select(func.count()).select_from(
            select(SysOperLog).where(SysOperLog.created_at >= thirty_days_ago).subquery()
        )
        recent_30 = int((await self.db.execute(recent_30_stmt)).scalar_one())

        # 最近90天记录数
        ninety_days_ago = now - timedelta(days=90)
        recent_90_stmt = select(func.count()).select_from(
            select(SysOperLog).where(SysOperLog.created_at >= ninety_days_ago).subquery()
        )
        recent_90 = int((await self.db.execute(recent_90_stmt)).scalar_one())

        return {
            "total_count": total,
            "last_30_days": recent_30,
            "last_90_days": recent_90,
        }

    async def get_all_stats(self) -> dict[str, dict[str, int]]:
        """获取所有维护相关的统计信息。

        返回：
        {
            "idempotency": 幂等键统计,
            "oper_log": 操作日志统计,
            "timestamp": 统计时间
        }
        """
        return {
            "idempotency": await self.get_idempotency_stats(),
            "oper_log": await self.get_oper_log_stats(),
            "timestamp": datetime.now(UTC).isoformat(),
        }

    async def get_system_health(self) -> dict[str, dict]:
        """获取系统健康状态，包括数据库连接池状态。"""
        from app.db.session import get_pool_status
        
        return {
            "database_pool": get_pool_status(),
            "data_stats": await self.get_all_stats(),
            "timestamp": datetime.now(UTC).isoformat(),
        }
