"""幂等业务服务。

为单据提交接口提供“先抢幂等锁、再执行业务、最后标记成功/失败”的流程。
若幂等键已存在且状态为成功，直接返回已缓存的响应体，不重复执行业务；
若状态为处理中/失败，允许重新尝试（覆盖旧记录）。
"""

import hashlib
import json
from typing import Any

from app.core.exceptions import AppException, ErrorCode
from app.models.idempotency import IdempotencyKey
from app.repositories.idempotency import IdempotencyRepository


class IdempotencyService:
    """幂等控制：确保同一幂等键的业务操作在窗口期内只执行一次。"""

    def __init__(self, db) -> None:
        self._repo = IdempotencyRepository(db)

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
        """对请求体做 SHA-256 哈希，用于判断重复请求的参数是否一致。"""
        raw = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
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

        if existing is not None:
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
        self, entity: IdempotencyKey, response_body: Any | None = None
    ) -> None:
        """将幂等键标记为成功，并可选地缓存响应体供重复请求返回。"""
        response_json = json.dumps(response_body, ensure_ascii=False) if response_body is not None else None
        await self._repo.mark_success(entity, response_json)

    async def mark_failed(self, entity: IdempotencyKey) -> None:
        """将幂等键标记为失败，允许后续重试。"""
        await self._repo.mark_failed(entity)

    async def get_cached_response(self, entity: IdempotencyKey) -> Any | None:
        """从已成功的幂等键记录中解析并返回缓存的响应体。"""
        if entity.response_body is None:
            return None
        return json.loads(entity.response_body)
