"""系统参数数据访问。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sys import SysConfig


class ConfigRepository:
    """系统参数数据访问（不含业务规则）。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_all(self) -> list[SysConfig]:
        """全部未删除参数，按 key 排序。"""
        stmt = select(SysConfig).where(SysConfig.is_deleted.is_(False)).order_by(SysConfig.key)
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_by_key(self, key: str) -> SysConfig | None:
        """按 key 查询参数（未删除）。"""
        return await self._db.scalar(
            select(SysConfig).where(SysConfig.key == key, SysConfig.is_deleted.is_(False))
        )
