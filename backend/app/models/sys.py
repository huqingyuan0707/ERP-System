"""系统管理模型（表名前缀 sys_）。"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import (
    BIGINT_PK,
    SoftDeleteMixin,
    TimestampMixin,
    UserMixin,
    VersionMixin,
)


class SysDept(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, Base):
    """部门。"""

    __tablename__ = "sys_dept"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(BIGINT_PK, ForeignKey("sys_dept.id"))
    leader: Mapped[str | None] = mapped_column(String(64))
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))


class SysUser(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, Base):
    """用户。"""

    __tablename__ = "sys_user"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    real_name: Mapped[str | None] = mapped_column(String(64))
    mobile: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(128))
    dept_id: Mapped[int | None] = mapped_column(BIGINT_PK, ForeignKey("sys_dept.id"))
    position: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # 令牌版本号：改密后 +1，使旧 Token 全部失效
    token_version: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_ip: Mapped[str | None] = mapped_column(String(64))
    remark: Mapped[str | None] = mapped_column(String(255))


class SysRole(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, Base):
    """角色。"""

    __tablename__ = "sys_role"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    data_scope: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))


class SysUserRole(Base):
    """用户-角色关联。"""

    __tablename__ = "sys_user_role"

    user_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("sys_user.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("sys_role.id", ondelete="CASCADE"), primary_key=True
    )


class SysPermission(TimestampMixin, SoftDeleteMixin, Base):
    """权限（目录/菜单/按钮）。"""

    __tablename__ = "sys_permission"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    parent_id: Mapped[int | None] = mapped_column(BIGINT_PK, ForeignKey("sys_permission.id"))
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    perm_code: Mapped[str | None] = mapped_column(String(128), index=True)
    route_path: Mapped[str | None] = mapped_column(String(255))
    component: Mapped[str | None] = mapped_column(String(255))
    icon: Mapped[str | None] = mapped_column(String(64))
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class SysRolePermission(Base):
    """角色-权限关联。"""

    __tablename__ = "sys_role_permission"

    role_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("sys_role.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("sys_permission.id", ondelete="CASCADE"), primary_key=True
    )


class SysRoleDept(Base):
    """角色-数据权限部门关联（数据范围自定义部门集，C-04）。"""

    __tablename__ = "sys_role_dept"

    role_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("sys_role.id", ondelete="CASCADE"), primary_key=True
    )
    dept_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("sys_dept.id", ondelete="CASCADE"), primary_key=True
    )


class SysDictType(TimestampMixin, SoftDeleteMixin, VersionMixin, Base):
    """字典类型。"""

    __tablename__ = "sys_dict_type"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        Index(
            "uq_dict_type_code",
            "code",
            unique=True,
            sqlite_where=text("is_deleted = 0"),
            postgresql_where=text("is_deleted = false"),
        ),
    )


class SysDictItem(TimestampMixin, SoftDeleteMixin, Base):
    """字典项。"""

    __tablename__ = "sys_dict_item"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    type_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(String(64), nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        Index(
            "uq_dict_item",
            "type_code",
            "value",
            unique=True,
            sqlite_where=text("is_deleted = 0"),
            postgresql_where=text("is_deleted = false"),
        ),
    )


class SysConfig(TimestampMixin, SoftDeleteMixin, Base):
    """系统参数（key-value）。"""

    __tablename__ = "sys_config"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    value: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        Index(
            "uq_config_key",
            "key",
            unique=True,
            sqlite_where=text("is_deleted = 0"),
            postgresql_where=text("is_deleted = false"),
        ),
    )


class SysBillNoSeq(Base):
    """单号序号（按业务 + 日期段计数，支撑每日重置）。"""

    __tablename__ = "sys_bill_no_seq"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    biz_key: Mapped[str] = mapped_column(String(64), nullable=False)
    period: Mapped[str] = mapped_column(String(8), nullable=False)
    current_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (UniqueConstraint("biz_key", "period", name="uq_bill_no_seq"),)


class SysOperLog(TimestampMixin, Base):
    """操作日志。"""

    __tablename__ = "sys_oper_log"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BIGINT_PK, index=True)
    module: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str | None] = mapped_column(String(64))
    method: Mapped[str | None] = mapped_column(String(16))
    url: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(String(64))
    params: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class SysLoginLog(TimestampMixin, Base):
    """登录日志（含失败记录，用于限流与锁定统计）。"""

    __tablename__ = "sys_login_log"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    message: Mapped[str | None] = mapped_column(String(255))


class SysRevokedToken(Base):
    """已撤销令牌（jti 黑名单，用于登出与轮换失效）。"""

    __tablename__ = "sys_revoked_token"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
