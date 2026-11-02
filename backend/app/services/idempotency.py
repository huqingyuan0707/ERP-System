"""幂等业务服务，execute 将业务写入和结果缓存置于同一事务。"""

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import AppException, ErrorCode
from app.models.idempotency import IdempotencyKey
from app.repositories.idempotency import IdempotencyRepository


class IdempotencyService:
    """幂等控制：确保同一幂等键的业务操作在窗口期内只执行一次。"""

    def __init__(self, db) -> None:
        self._db = db
        self._repo = IdempotencyRepository(db)

    async def execute(
        self,
        *,
        tenant_id: int,
        user_id: int,
        biz_type: str,
        idempotency_key: str | None = None,
        request_body: Any = None,
        operation: Callable[[], Awaitable[Any]],
        ttl_seconds: int = 86400,
    ) -> Any:
        """执行不自行提交的 operation；成功结果与业务数据原子提交，失败整体回滚。"""
        try:
            entity = None
            if idempotency_key is not None:
                idempotency_key = idempotency_key.strip()
                if not idempotency_key or len(idempotency_key) > 128:
                    raise AppException(ErrorCode.VALIDATION_ERROR, "幂等键须为 1 至 128 个非空字符")
                key = self.build_key(tenant_id, user_id, biz_type, idempotency_key)
                if len(key) > 255 or len(biz_type) > 64 or ttl_seconds <= 0:
                    raise AppException(ErrorCode.VALIDATION_ERROR, "幂等参数超出允许范围")
                if self._db.bind.dialect.name == "postgresql":
                    acquired = (
                        await self._db.execute(
                            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
                            {"key": key},
                        )
                    ).scalar_one()
                    if not acquired:
                        raise AppException(ErrorCode.CONFLICT, "相同幂等请求正在处理中，请稍后重试")
                existing = await self._repo.get_by_key(key)
                if existing is not None and not self._expired(existing):
                    if existing.request_body_hash != self.hash_body(request_body):
                        raise AppException(
                            ErrorCode.CONFLICT, "幂等键已被其他参数占用，请更换幂等键"
                        )
                    if existing.status == 1:
                        result = await self.get_cached_response(existing)
                        await self._db.commit()
                        return result
                    if existing.status == 2:
                        raise AppException(ErrorCode.CONFLICT, "相同幂等请求正在处理中，请稍后重试")
                if existing is not None:
                    await self._repo.delete_by_key(key)
                try:
                    entity = await self._repo.create(
                        key=key,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        biz_type=biz_type,
                        biz_no=idempotency_key,
                        request_body_hash=self.hash_body(request_body),
                        ttl_seconds=ttl_seconds,
                    )
                except IntegrityError as exc:
                    raise AppException(
                        ErrorCode.CONFLICT, "相同幂等请求已提交，请重试获取结果"
                    ) from exc
            result = await operation()
            if isinstance(result, BaseModel):
                result = result.model_dump(mode="json", by_alias=True)
            result = json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))
            await self.confirm_success(entity, result)
            await self._db.commit()
            return result
        except IntegrityError as exc:
            await self._db.rollback()
            raise AppException(ErrorCode.CONFLICT, "数据已被占用或引用，请刷新后重试") from exc
        except BaseException:
            await self._db.rollback()
            raise

    @staticmethod
    def _expired(entity: IdempotencyKey) -> bool:
        expires = entity.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return expires <= datetime.now(UTC)

    @staticmethod
    def build_key(
        tenant_id: int,
        user_id: int,
        biz_type: str,
        idempotency_key: str,
    ) -> str:
        """构造幂等键字符串：idem:{biz_type}:{tenant_id}:{user_id}:{idempotency_key}。"""
        return f"idem:{biz_type}:{tenant_id}:{user_id}:{idempotency_key}"

    @staticmethod
    def hash_body(body: Any) -> str:
        """对请求体做 SHA-256 哈希，用于判断重复请求的参数是否一致。

        请求体可能是普通字典，也可能是 Pydantic 模型；
        Pydantic 模型不能直接 json.dumps（会抛 TypeError），
        所以先用 model_dump(mode="json") 转成可 JSON 序列化的字典。
        """
        payload = body.model_dump(mode="json") if isinstance(body, BaseModel) else body
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    async def check_and_register(
        self,
        *,
        tenant_id: int,
        user_id: int,
        biz_type: str,
        idempotency_key: str,
        request_body: Any | None = None,
        ttl_seconds: int = 86400,
    ) -> IdempotencyKey | None:
        """检查幂等键并注册“处理中”记录。

        返回值：
          - IdempotencyKey：本次请求应执行业务（键不存在，或存在但为失败状态）；
            调用方在业务完成后应调用 `confirm_success` 或 `mark_failed`。
          - None：幂等键已存在且状态为成功，表示重复提交，调用方应直接返回
            已缓存的响应体，不再执行业务。

        异常：
          - 若幂等键已存在且状态为成功但请求体哈希不一致，抛出 409，
            提示客户端使用不同的幂等键。
        """
        key = self.build_key(tenant_id, user_id, biz_type, idempotency_key)
        existing = await self._repo.get_by_key(key)

        if existing is not None and self._expired(existing):
            await self._repo.delete_by_key(key)
            existing = None
        if existing is not None:
            if existing.status == 2:
                raise AppException(ErrorCode.CONFLICT, "相同幂等请求正在处理中，请稍后重试")
            if existing.status == 1:  # 成功
                # 同一幂等键、不同请求体：拒绝，避免误返回错误结果
                if (
                    request_body is not None
                    and existing.request_body_hash is not None
                    and self.hash_body(request_body) != existing.request_body_hash
                ):
                    raise AppException(
                        ErrorCode.CONFLICT,
                        "幂等键已被其他参数的请求占用，请更换幂等键后重试",
                    )
                return None  # 重复提交，直接返回缓存结果
            if existing.status == 3:  # 失败：允许重试，覆盖旧记录
                await self._repo.delete_by_key(key)
                # 继续往下创建新记录

        # 不存在或已失败：创建“处理中”记录
        request_body_hash = self.hash_body(request_body) if request_body is not None else None
        return await self._repo.create(
            key=key,
            tenant_id=tenant_id,
            user_id=user_id,
            biz_type=biz_type,
            biz_no=idempotency_key,
            request_body_hash=request_body_hash,
            ttl_seconds=ttl_seconds,
        )

    async def confirm_success(
        self, entity: IdempotencyKey | None, response_body: Any | None = None
    ) -> None:
        """将幂等键标记为成功，并可选地缓存响应体供重复请求返回。

        ``entity`` 允许为 None：调用方未提供幂等键时没有落库记录，
        此时直接返回即可（业务本身已经执行成功，不能因为没有幂等记录而报错）。
        """
        if entity is None:
            # 没有幂等记录可更新，直接返回，不影响主流程
            return
        if isinstance(response_body, BaseModel):
            response_body = response_body.model_dump(mode="json", by_alias=True)
        response_json = (
            json.dumps(response_body, ensure_ascii=False, allow_nan=False)
            if response_body is not None
            else None
        )
        await self._repo.mark_success(entity, response_json)

    async def mark_failed(self, entity: IdempotencyKey) -> None:
        """将幂等键标记为失败，允许后续重试。"""
        await self._repo.mark_failed(entity)

    async def get_cached_response(self, entity: IdempotencyKey) -> Any | None:
        """从已成功的幂等键记录中解析并返回缓存的响应体。"""
        if entity.response_body is None:
            return None
        return json.loads(entity.response_body)
