"""SQLAlchemy 公共 Mixin 与跨库主键类型。"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, false, func
from sqlalchemy.orm import Mapped, mapped_column


# PostgreSQL 使用 BIGSERIAL；SQLite 退化为 INTEGER 以保证主键自增
BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")


class TimestampMixin:
    """创建/更新时间。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UserMixin:
    """创建人/更新人。"""

    created_by: Mapped[int | None] = mapped_column(BIGINT_PK)
    updated_by: Mapped[int | None] = mapped_column(BIGINT_PK)


class SoftDeleteMixin:
    """逻辑删除标记。"""

    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )


class VersionMixin:
    """乐观锁版本号。"""

    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)


class TenantMixin:
    """多租户隔离字段 Mixin，所有多租户业务表建议继承。"""

    tenant_id: Mapped[int] = mapped_column(
        BIGINT_PK,
        nullable=False,
        index=True,
        server_default="1",
        comment="租户 ID",
    )
