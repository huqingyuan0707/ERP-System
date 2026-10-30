"""撤销令牌数据访问。"""

from datetime import datetime

from sqlalchemy import select

from app.models.sys import SysRevokedToken
from app.repositories.base import BaseRepository


class RevokedTokenRepository(BaseRepository[SysRevokedToken]):
    """撤销令牌（jti 黑名单）仓储。"""

    model = SysRevokedToken

    async def exists_by_jti(self, jti: str) -> bool:
        """判断 jti 是否已被撤销。"""
        stmt = select(SysRevokedToken.id).where(SysRevokedToken.jti == jti).limit(1)
        return (await self.db.execute(stmt)).scalar_one_or_none() is not None

    async def create(self, jti: str, expires_at: datetime) -> SysRevokedToken:
        """登记一个已撤销的 jti。"""
        return await self.add(SysRevokedToken(jti=jti, expires_at=expires_at))
