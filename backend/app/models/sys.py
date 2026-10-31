"""系统管理模型（表名前缀 sys_）。

本文件定义系统管理模块的所有 SQLAlchemy ORM 模型，每个类对应数据库中的一张表。
模型定义了表名、字段名、字段类型、约束（唯一、外键、索引等），是代码与数据库之间的映射层。

命名规范：
  - 类名：PascalCase，如 SysUser、SysRole
  - 表名：sys_ 前缀 + 小写下划线，如 sys_user、sys_role
  - 字段名：snake_case，如 username、password_hash、created_at
  - 主键：id bigserial（通过 BIGINT_PK 定义，PostgreSQL 用 BIGINT，SQLite 退化为 INTEGER）

公共字段（通过 Mixin 继承，下表不再重复列出）：
  - id / created_at / created_by / updated_at / updated_by / is_deleted / version
  - 具体定义见 app/models/mixins.py：

    * TimestampMixin：created_at、updated_at（自动记录创建/更新时间）
    * UserMixin：created_by、updated_by（记录操作人 ID）
    * SoftDeleteMixin：is_deleted（逻辑删除标记，True 表示已删除）
    * VersionMixin：version（乐观锁版本号，用于并发控制）
"""

# 用于 Mapped[datetime] 类型标注，让 last_login_at 等时间字段有明确类型
from datetime import datetime

from sqlalchemy import (
    DateTime,  # 时间/日期列类型
    ForeignKey,  # 外键声明，指定关联的表与列
    Index,  # 自定义索引，这里主要用于“部分唯一索引”
    Integer,  # 整数列
    String,  # 变长字符串，必须指定最大长度
    Text,  # 不限长度的文本，适合存 JSON/长说明
    UniqueConstraint,  # 组合唯一约束，用于多列联合唯一
    func,  # 调用数据库函数，如 func.now() 取当前时间
    text,  # 原样 SQL 片段，用于索引的 WHERE 条件（部分索引）
)
from sqlalchemy.orm import Mapped, mapped_column  # 2.0 类型化映射写法

from app.db.base import Base  # 所有模型的基类，继承后才会被注册为 ORM 映射
from app.models.mixins import (
    BIGINT_PK,  # 跨库主键类型：PostgreSQL 用 BIGINT，SQLite 退化为 INTEGER
    SoftDeleteMixin,  # 公共列：is_deleted（逻辑删除标记）
    TimestampMixin,  # 公共列：created_at / updated_at（自动记录时间）
    UserMixin,  # 公共列：created_by / updated_by（记录操作人）
    VersionMixin,  # 公共列：version（乐观锁版本号）
)


# 部门表：sys_dept，通过 parent_id 自关联支持多级部门树
class SysDept(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, Base):
    """部门表：sys_dept。

    字段含义：
      - id：部门主键
      - name：部门名称
      - parent_id：父部门 ID，NULL 表示顶级部门，支持多级部门树
      - leader：部门负责人姓名
      - sort：排序字段，数字越小越靠前
      - status：状态，1=启用，0=禁用
      - remark：备注

    关系：SysUser.dept_id -> SysDept.id（用户所属部门）
    """

    # 类与数据库表名绑定，SQLAlchemy 据此知道读写哪张表
    __tablename__ = "sys_dept"

    # 主键：自增，插入时由数据库生成 ID
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)  # 部门名称，必填
    # 父部门 ID；自关联到本表主键，为 NULL 表示顶级部门
    parent_id: Mapped[int | None] = mapped_column(BIGINT_PK, ForeignKey("sys_dept.id"))
    leader: Mapped[str | None] = mapped_column(String(64))  # 负责人姓名，可空
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 排序号，越小越前
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 1=启用，0=禁用
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注，可空


# 用户表：sys_user，系统登录与权限体系的核心表
class SysUser(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, Base):
    """用户表：sys_user。

    这是系统管理模块最核心的表，存储每个登录用户的基本信息。

    字段含义：
      - id：用户主键
      - username：登录账号，唯一，建有索引，支持快速按用户名查询（登录时用）
      - password_hash：密码哈希值，bcrypt 加密后存储，明文不进数据库
      - real_name：真实姓名，可选
      - mobile：手机号，可选
      - email：邮箱，可选
      - dept_id：所属部门 ID，外键指向 sys_dept.id，可为空
      - position：岗位/职位，可选
      - status：账号状态，1=启用，0=禁用，禁用后用户无法登录
      - token_version：令牌版本号，默认 0。修改密码或禁用启用时 +1，
        使已签发的 JWT Token 立即失效，强制用户重新登录
      - last_login_at：最后登录时间
      - last_login_ip：最后登录 IP
      - remark：备注

    关系：
      - SysUser.dept_id -> SysDept.id（用户所属部门）
      - SysUser.id -> SysUserRole.user_id（用户-角色关联，一对多）
    """

    # 映射到表 sys_user
    __tablename__ = "sys_user"

    # 主键：自增，插入时由数据库生成 ID
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    # 登录账号：唯一索引，登录与新增查重都按它查询；唯一约束也是数据库层的最终防重
    username: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(
        String(255), nullable=False
    )  # 只存 bcrypt 哈希，明文永不落库
    real_name: Mapped[str | None] = mapped_column(String(64))  # 真实姓名，可空
    mobile: Mapped[str | None] = mapped_column(String(32))  # 手机号，可空
    email: Mapped[str | None] = mapped_column(String(128))  # 邮箱，可空
    dept_id: Mapped[int | None] = mapped_column(
        BIGINT_PK, ForeignKey("sys_dept.id")
    )  # 所属部门，可空，外键指向 sys_dept.id
    position: Mapped[str | None] = mapped_column(String(64))  # 岗位，可空
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 1=启用，0=禁用
    # 令牌版本号：改密后 +1，使旧 Token 全部失效
    token_version: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    # 最后登录时间，带时区；失败登录不更新
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_ip: Mapped[str | None] = mapped_column(String(64))  # 最后登录 IP
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注，可空


# 角色表：sys_role，用于权限分组（一个用户可挂多个角色）
class SysRole(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, Base):
    """角色表：sys_role。

    角色用于权限分组，一个用户可以有多个角色。

    字段含义：
      - id：角色主键
      - code：角色编码，唯一，如 "admin"、"viewer"、"manager"，用于代码中判断权限
      - name：角色名称，如 "超级管理员"、"只读用户"
      - data_scope：数据范围，1=本人，2=本部门（含下级），3=全部
      - status：状态，1=启用，0=禁用
      - remark：备注

    关系：
      - SysRole.id -> SysUserRole.role_id（用户-角色关联）
      - SysRole.id -> SysRolePermission.permission_id（角色-权限关联）
      - SysRole.id -> SysRoleDept.role_id（角色-数据范围部门关联）
    """

    # 映射到表 sys_role
    __tablename__ = "sys_role"

    # 主键：自增，插入时由数据库生成 ID
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    # 角色编码：唯一且建索引，代码里用 code 判断角色（如 admin）
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)  # 角色中文名，必填
    # 数据范围：1=本人，2=本部门（含下级），3=全部
    data_scope: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 1=启用，0=禁用
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注，可空


# 用户-角色关联表：sys_user_role，用联合主键实现多对多
class SysUserRole(Base):
    """用户-角色关联表：sys_user_role。

    实现用户和角色的多对多关系：
      - 一个用户可以有多个角色
      - 一个角色可以被多个用户拥有

    字段：
      - user_id：用户 ID，外键指向 sys_user.id，ondelete="CASCADE" 表示用户删除时自动清理关联
      - role_id：角色 ID，外键指向 sys_role.id，ondelete="CASCADE" 表示角色删除时自动清理关联
    主键：(user_id, role_id) 联合主键
    """

    # 映射到中间表 sys_user_role
    __tablename__ = "sys_user_role"

    # 用户 ID：外键→sys_user.id；联合主键之一，重复分配同一角色会被主键拒绝
    user_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("sys_user.id", ondelete="CASCADE"), primary_key=True
    )
    # 角色 ID：外键→sys_role.id；联合主键之二，组合起来保证 (用户, 角色) 唯一
    role_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("sys_role.id", ondelete="CASCADE"), primary_key=True
    )


# 权限表：sys_permission，目录/菜单/按钮三级树，驱动前端路由与按钮显隐
class SysPermission(TimestampMixin, SoftDeleteMixin, Base):
    """权限表：sys_permission。

    权限分三级：目录 / 菜单 / 按钮，用于前端动态路由和按钮显隐。

    字段含义：
      - id：权限主键
      - parent_id：父权限 ID，构成树形结构，根节点 parent_id 为 NULL
      - name：权限名称，如 "系统管理"、"用户管理"、"新增用户"
      - type：权限类型，1=目录，2=菜单，3=按钮
      - perm_code：权限码，如 "sys:user:list"、"sys:user:add"，后端接口校验时用
      - route_path：前端路由路径，如 "/system/user"
      - component：前端组件路径，如 "system/user/index"
      - icon：图标名称，供前端 Element Plus 图标组件使用
      - sort：排序字段

    关系：
      - SysPermission.id -> SysRolePermission.permission_id（角色-权限关联）
      - SysPermission.parent_id -> SysPermission.id（自关联，构成树）
    """

    # 映射到表 sys_permission
    __tablename__ = "sys_permission"

    # 主键：自增，插入时由数据库生成 ID
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    # 父权限 ID：自关联本表主键，NULL 表示根节点，从而构成目录→菜单→按钮的树
    parent_id: Mapped[int | None] = mapped_column(
        BIGINT_PK, ForeignKey("sys_permission.id")
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)  # 权限名称，必填
    type: Mapped[int] = mapped_column(Integer, default=2, nullable=False)  # 1=目录 2=菜单 3=按钮
    # 权限码：后端 require_perm("sys:user:list") 校验用；建索引便于按码查询
    perm_code: Mapped[str | None] = mapped_column(String(128), index=True)
    route_path: Mapped[str | None] = mapped_column(String(255))  # 前端路由路径，可空
    component: Mapped[str | None] = mapped_column(String(255))  # 前端组件路径，可空
    icon: Mapped[str | None] = mapped_column(String(64))  # 图标名，可空
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 排序号，越小越前


# 角色-权限关联表：sys_role_permission，多对多
class SysRolePermission(Base):
    """角色-权限关联表：sys_role_permission。

    实现角色和权限的多对多关系：
      - 一个角色可以拥有多个权限（目录/菜单/按钮）
      - 一个权限可以被多个角色拥有

    字段：
      - role_id：角色 ID，外键指向 sys_role.id
      - permission_id：权限 ID，外键指向 sys_permission.id
    主键：(role_id, permission_id) 联合主键
    """

    # 映射到中间表 sys_role_permission
    __tablename__ = "sys_role_permission"

    # 角色 ID：外键→sys_role.id；联合主键之一
    role_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("sys_role.id", ondelete="CASCADE"), primary_key=True
    )
    # 权限 ID：外键→sys_permission.id；联合主键之二，同一角色同一权限不会重复
    permission_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("sys_permission.id", ondelete="CASCADE"), primary_key=True
    )


# 角色-数据范围部门关联表：sys_role_dept，data_scope=2 时额外指定的可见部门
class SysRoleDept(Base):
    """角色-数据范围部门关联表：sys_role_dept。

    支持“本部门”数据范围时的自定义部门集合。

    字段含义：
      - role_id：角色 ID
      - dept_id：部门 ID

    用途：当角色 data_scope=2（本部门）时，除了用户所在部门子树，
    还可以通过这张表额外指定可见部门。多角色取最大范围。
    """

    # 映射到中间表 sys_role_dept
    __tablename__ = "sys_role_dept"

    # 角色 ID：外键→sys_role.id；联合主键之一
    role_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("sys_role.id", ondelete="CASCADE"), primary_key=True
    )
    # 部门 ID：外键→sys_dept.id；联合主键之二，表示该角色额外可见的部门
    dept_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("sys_dept.id", ondelete="CASCADE"), primary_key=True
    )


# 字典类型表：sys_dict_type，字典分类（如 gender=性别）
class SysDictType(TimestampMixin, SoftDeleteMixin, VersionMixin, Base):
    """字典类型表：sys_dict_type。

    字典分类，如 "性别"、"状态"、"单据类型"。

    字段：
      - code：字典类型编码，如 "gender"、"status"
      - name：字典类型名称，如 "性别"、"启用禁用状态"
      - status：状态，1=启用，0=禁用
    """

    # 映射到表 sys_dict_type
    __tablename__ = "sys_dict_type"

    # 主键：自增，插入时由数据库生成 ID
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    # 类型编码：建索引；唯一性由下面的部分唯一索引负责（软删后可复用同名编码）
    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)  # 类型名称，必填
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 1=启用，0=禁用
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注，可空

    # 部分唯一索引：只约束未软删的记录，使被删除的 code 可以重新使用
    __table_args__ = (
        Index(
            "uq_dict_type_code",  # 索引名
            "code",  # 约束列
            unique=True,  # 唯一
            sqlite_where=text("is_deleted = 0"),  # SQLite 的部分索引条件
            postgresql_where=text("is_deleted = false"),  # PostgreSQL 的部分索引条件
        ),
    )


# 字典项表：sys_dict_item，具体字典值（如 gender 下的 男/女）
class SysDictItem(TimestampMixin, SoftDeleteMixin, Base):
    """字典项表：sys_dict_item。

    具体的字典值，如 性别类型下的 "男/女"，状态类型下的 "启用/禁用"。

    字段：
      - type_code：所属字典类型编码，外键逻辑关联 sys_dict_type.code
      - label：显示标签，如 "男"、"启用"
      - value：存储值，如 "M"、"F"，"1"、"0"
      - sort：排序字段
      - status：状态，1=启用，0=禁用
    """

    # 映射到表 sys_dict_item
    __tablename__ = "sys_dict_item"

    # 主键：自增，插入时由数据库生成 ID
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    # 所属类型编码：逻辑外键（不建 ForeignKey），按值关联 sys_dict_type.code
    type_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)  # 显示文本，如“男”
    value: Mapped[str] = mapped_column(String(64), nullable=False)  # 存储值，如 M / F
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 排序号
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 1=启用，0=禁用
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注，可空

    # 部分唯一索引：同一类型下 value 唯一（软删的记录不参与约束）
    __table_args__ = (
        Index(
            "uq_dict_item",  # 索引名
            "type_code",  # 联合列之一：类型编码
            "value",  # 联合列之二：字典值
            unique=True,  # 组合唯一
            sqlite_where=text("is_deleted = 0"),  # SQLite 部分索引条件
            postgresql_where=text("is_deleted = false"),  # PostgreSQL 部分索引条件
        ),
    )


# 系统参数表：sys_config，key-value 形式的全局配置
class SysConfig(TimestampMixin, SoftDeleteMixin, Base):
    """系统参数表：sys_config。

    存储系统配置项，key-value 形式，如：
      - 单据编号规则：billno.PO = "PO,4"
      - 公司信息、业务参数等

    字段：
      - key：配置项键名，唯一
      - value：配置项值
      - name：配置项中文名称
    """

    # 映射到表 sys_config
    __tablename__ = "sys_config"

    # 主键：自增，插入时由数据库生成 ID
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    # 配置键名：建索引；唯一性由部分唯一索引保证（软删后可重建同名键）
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    value: Mapped[str | None] = mapped_column(Text)  # 配置值，长文本，可空
    name: Mapped[str] = mapped_column(String(128), nullable=False)  # 配置中文名，必填
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注，可空

    # 部分唯一索引：未软删的 key 唯一
    __table_args__ = (
        Index(
            "uq_config_key",  # 索引名
            "key",  # 约束列
            unique=True,  # 唯一
            sqlite_where=text("is_deleted = 0"),  # SQLite 部分索引条件
            postgresql_where=text("is_deleted = false"),  # PostgreSQL 部分索引条件
        ),
    )


# 单号序号表：sys_bill_no_seq，按 (业务键, 日期) 维护自增序号
class SysBillNoSeq(Base):
    """单号序号表：sys_bill_no_seq。

    支持单号每日重置，如 PO202610300001。
    按 biz_key + period（业务键 + 日期）维护当前序号，UPSERT 递增保证并发下不重复。

    字段：
      - biz_key：业务键，如 "pur_order"（采购订单）、"sal_order"（销售订单）
      - period：日期段，格式 yyyyMMdd，如 "20261030"
      - current_seq：当前序号，递增使用
    """

    # 映射到表 sys_bill_no_seq
    __tablename__ = "sys_bill_no_seq"

    # 主键：自增，插入时由数据库生成 ID
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    biz_key: Mapped[str] = mapped_column(String(64), nullable=False)  # 业务键，如 pur_order
    period: Mapped[str] = mapped_column(String(8), nullable=False)  # 日期段 yyyyMMdd
    current_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 当前序号

    # 组合唯一约束：(业务键, 日期) 唯一，保证同一业务同一天只有一行计数
    __table_args__ = (UniqueConstraint("biz_key", "period", name="uq_bill_no_seq"),)


# 操作日志表：sys_oper_log，记录增删改、审核、确认等关键动作
class SysOperLog(TimestampMixin, Base):
    """操作日志表：sys_oper_log。

    记录所有增删改、审核、确认、核销类操作，用于审计和排查问题。

    字段：
      - user_id：操作人 ID
      - module：模块名称，如 "系统管理"、"采购管理"
      - action：动作描述，如 "新增用户"、"确认入库"
      - method：HTTP 方法，如 POST、PUT
      - url：请求 URL
      - ip：操作人 IP
      - params：参数摘要（JSON 字符串，敏感字段如密码已脱敏）
      - result：操作结果摘要
      - duration_ms：耗时（毫秒）
      - status：状态，1=成功，0=失败
    """

    # 映射到表 sys_oper_log
    __tablename__ = "sys_oper_log"

    # 主键：自增，插入时由数据库生成 ID
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BIGINT_PK, index=True)  # 操作人，可空
    module: Mapped[str | None] = mapped_column(String(64))  # 模块名，如“系统管理”
    action: Mapped[str | None] = mapped_column(String(64))  # 动作，如“新增用户”
    method: Mapped[str | None] = mapped_column(String(16))  # HTTP 方法
    url: Mapped[str | None] = mapped_column(String(255))  # 请求路径
    ip: Mapped[str | None] = mapped_column(String(64))  # 操作 IP
    params: Mapped[str | None] = mapped_column(Text)  # 参数摘要 JSON，密码已脱敏
    result: Mapped[str | None] = mapped_column(Text)  # 结果摘要
    duration_ms: Mapped[int | None] = mapped_column(Integer)  # 耗时毫秒
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 1=成功，0=失败


# 登录日志表：sys_login_log，记录每次登录尝试（成功/失败）
class SysLoginLog(TimestampMixin, Base):
    """登录日志表：sys_login_log。

    记录每次登录尝试，包括成功和失败，用于安全审计和账号锁定统计。

    字段：
      - username：尝试登录的账号
      - ip：登录 IP
      - user_agent：浏览器 UA 字符串
      - status：登录状态，1=成功，0=失败
      - message：失败原因，如 "密码错误"、"账号已禁用"、"验证码错误"
    """

    # 映射到表 sys_login_log
    __tablename__ = "sys_login_log"

    # 主键：自增，插入时由数据库生成 ID
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    # 尝试登录的账号：不存 user_id，因为失败时可能根本不存在对应账号；建索引供限流统计
    username: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))  # 登录 IP
    user_agent: Mapped[str | None] = mapped_column(String(512))  # 浏览器 UA
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 1=成功，0=失败
    message: Mapped[str | None] = mapped_column(String(255))  # 失败原因


# 异步任务表：sys_task，记录导入导出等后台任务的进度与产物
class SysTask(TimestampMixin, Base):
    """异步任务表：sys_task。

    记录 Excel 导入导出、报表生成等异步任务的执行状态，供前端轮询进度与下载结果。

    字段：
      - task_id：任务唯一标识（UUID 字符串），前端据此轮询
      - task_type：任务类型，如 "goods_export"、"goods_import"、"stock_export"、"statement_export"
      - status：任务状态，1=待执行，2=执行中，3=成功，4=失败
      - params：任务参数（JSON 字符串），如筛选条件、导出列配置
      - file_path：生成文件的存储路径，成功后可供下载
      - error_msg：失败时的错误信息
      - progress：当前进度百分比（0-100）
      - created_by：创建人用户 ID
      - started_at / finished_at：任务开始与完成时间
    """

    # 映射到表 sys_task
    __tablename__ = "sys_task"

    # 主键：自增，插入时由数据库生成 ID
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    # 任务 UUID：唯一且建索引，前端凭它轮询任务进度
    task_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)  # 任务类型
    # 任务状态：1=待执行，2=执行中，3=成功，4=失败
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    params: Mapped[str | None] = mapped_column(Text)  # 任务参数 JSON
    file_path: Mapped[str | None] = mapped_column(String(512))  # 产物路径，供下载
    error_msg: Mapped[str | None] = mapped_column(Text)  # 失败原因
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 进度 0-100
    # 创建人：本表自己声明，未继承 UserMixin，避免列名冲突
    created_by: Mapped[int | None] = mapped_column(BIGINT_PK, index=True)
    # 开始时间，带时区
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 完成时间，带时区
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# 已撤销令牌表：sys_revoked_token，JWT 黑名单（登出/轮换时写入）
class SysRevokedToken(Base):
    """已撤销令牌表：sys_revoked_token。

    存储已登出或已轮换的 refresh token 的 jti（JWT ID），用于黑名单校验。
    用户退出登录或修改密码后，旧的 refresh token 会被记入此表，后续使用会被拒绝。

    字段：
      - jti：令牌唯一标识，唯一索引
      - expires_at：令牌过期时间，过期后可清理
      - created_at：记录创建时间
    """

    # 映射到表 sys_revoked_token
    __tablename__ = "sys_revoked_token"

    # 主键：自增，插入时由数据库生成 ID
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    # 令牌唯一标识 jti：唯一且建索引，作为黑名单查询键
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # 令牌过期时间：过期后该记录可被清理，不再需要拦截
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 记录创建时间：本表未继承 TimestampMixin，所以手写，且只需创建时间
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
