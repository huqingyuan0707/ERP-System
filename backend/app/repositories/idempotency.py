"""幂等键数据访问层。"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.idempotency import IdempotencyKey


class IdempotencyRepository:
    """幂等键数据访问：查询、插入、标记处理状态、清理过期记录。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_key(self, key: str) -> IdempotencyKey | None:
        """按幂等键查询记录。"""
        stmt = select(IdempotencyKey).where(IdempotencyKey.key == key)
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        *,
        key: str,
        tenant_id: int,
        user_id: int,
        biz_type: str,
        biz_no: str | None = None,
        request_body_hash: str | None = None,
        ttl_seconds: int = 86400,
    ) -> IdempotencyKey:
        """插入一条状态为“处理中”的幂等键记录。"""
        now = datetime.now(timezone.utc)
        entity = IdempotencyKey(
            key=key,
            tenant_id=tenant_id,
            user_id=user_id,
            biz_type=biz_type,
            biz_no=biz_no,
            request_body_hash=request_body_hash,
            status=2,  # 处理中
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self._db.add(entity)
        await self._db.flush()
        return entity

    async def mark_success(
        self, entity: IdempotencyKey, response_body: str | None = None
    ) -> None:
        """将幂等键标记为成功，并可选地写入响应体。"""
        entity.status = 1  # 成功
        if response_body is not None:
            entity.response_body = response_body
        await self._db.flush()

    async def mark_failed(self, entity: IdempotencyKey) -> None:
        """将幂等键标记为失败，以便后续重试可重新入库。"""
        entity.status = 3  # 失败
        await self._db.flush()

    async def delete_by_key(self, key: str) -> None:
        """按幂等键删除记录（用于测试清理）。"""
        entity = await self.get_by_key(key)
        if entity is not None:
            await self._db.delete(entity)
            await self._db.flush()
