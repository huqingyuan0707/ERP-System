"""幂等键模型（表名前缀 idem_）。

用于单据提交接口的幂等防重：同一租户、同一操作人、同一业务幂等键
在指定窗口期内只允许成功入库一次，重复请求返回已有结果或拒绝重复提交。
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import BIGINT_PK


class IdempotencyKey(Base):
    """幂等键记录：idem_idempotency_key。

    字段：
      - key：幂等键（如 "idem:change_stock:tenant:user:bizNo"），全局唯一
      - tenant_id：所属租户，用于多租户隔离与清理
      - user_id：操作人 ID
      - biz_type：业务类型，如 "change_stock"、"lock_stock"
      - biz_no：业务单号，与幂等键一起确定幂等语义
      - request_body_hash：请求体哈希，用于判断重复请求是否参数一致
      - response_body：首次成功处理的响应体（JSON），重复请求时可返回
      - status：处理状态，1=成功，2=处理中，3=失败
      - created_at / expires_at：创建时间与过期时间，过期后可清理
    """

    __tablename__ = "idem_idempotency_key"
    __table_args__ = (
        Index("uq_idempotency_key", "key", unique=True),
        Index("ix_idempotency_tenant", "tenant_id"),
        Index("ix_idempotency_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(255), nullable=False, comment="幂等键，全局唯一")
    tenant_id: Mapped[int] = mapped_column(
        BIGINT_PK, nullable=False, index=True, comment="租户 ID"
    )
    user_id: Mapped[int] = mapped_column(
        BIGINT_PK, nullable=False, index=True, comment="操作人 ID"
    )
    biz_type: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="业务类型，如 change_stock、lock_stock"
    )
    biz_no: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="业务单号，与幂等键共同确定幂等语义"
    )
    request_body_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="请求体 SHA-256 前16位，用于参数一致性校验"
    )
    response_body: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="首次成功响应体 JSON，重复请求时返回"
    )
    status: Mapped[int] = mapped_column(
        default=1, nullable=False, comment="处理状态：1=成功，2=处理中，3=失败"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="幂等键过期时间，过期后可清理"
    )
