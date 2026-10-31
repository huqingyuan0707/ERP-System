"""用户相关 Schema。

本文件定义用户管理接口的“请求/响应数据模型”（Pydantic Schema）。
Schema 是前后端的“合同”：
  - 前端发什么字段、字段格式对不对，由请求 Schema 校验；
  - 后端返回什么字段、哪些字段不能返回（如密码哈希），由响应 Schema 决定。

命名规范（与代码风格文档一致）：
  - 新增：UserCreate
  - 部分更新：UserUpdate
  - 返回给前端：UserOut
  - 列表查询参数：UserQuery

字段命名：
  - Python 类属性用 snake_case，如 role_ids、real_name；
  - 对外 API 字段自动转为 camelCase，如 roleIds、realName；
  - 转换逻辑在 app/schemas/common.py 的 to_camel 函数中实现。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageQuery, to_camel


class UserCreate(BaseModel):
    """新增用户请求体。

    前端调用 POST /api/v1/sys/users 时，请求 JSON 必须符合这个模型。
    如果字段缺失、类型不对或长度超限，FastAPI 会直接返回 422 错误，
    不需要在 Service 层手写校验代码。
    """

    model_config = ConfigDict(
        alias_generator=to_camel,   # 把 Python 的 snake_case 自动映射为 API 的 camelCase
        populate_by_name=True,      # 允许前端直接用 snake_case 字段名发送数据
    )

    username: str = Field(min_length=1, max_length=64)     # 登录账号，1~64 字符，不能为空
    password: str = Field(min_length=6, max_length=128)    # 登录密码，至少 6 位，最多 128 位
    real_name: str | None = Field(default=None, max_length=64)  # 真实姓名，可选
    mobile: str | None = Field(default=None, max_length=32)   # 手机号，可选
    email: str | None = Field(default=None, max_length=128)   # 邮箱，可选
    dept_id: int | None = None                             # 所属部门 ID，可选
    position: str | None = Field(default=None, max_length=64) # 岗位/职位，可选
    status: int = 1                                        # 账号状态：1=启用，0=禁用，默认启用
    role_ids: list[int] = Field(default_factory=list)       # 角色 ID 列表，一个用户可有多个角色


class UserUpdate(BaseModel):
    """更新用户请求体（部分更新）。

    和 UserCreate 的区别：
      - 所有字段都有 default=None，表示“前端只传要改的字段，不传的字段不动”；
      - 用于 PUT /api/v1/sys/users/{user_id} 接口。
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    password: str | None = Field(default=None, min_length=6, max_length=128)
    real_name: str | None = Field(default=None, max_length=64)
    mobile: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=128)
    dept_id: int | None = None
    position: str | None = Field(default=None, max_length=64)
    status: int | None = None                                # 1=启用，0=禁用，不传则保持原样
    role_ids: list[int] | None = None                        # 不传则保持原有角色不变


class UserOut(BaseModel):
    """用户出参模型，返回给前端的数据。

    关键约定：
      - 不包含 password_hash 字段！密码哈希绝对不能返回给前端。
      - from_attributes=True 表示可以直接从 SQLAlchemy ORM 模型对象转换。
    """

    model_config = ConfigDict(
        from_attributes=True,      # 允许从 ORM 模型直接生成 Pydantic 模型
        alias_generator=to_camel,
        populate_by_name=True,
    )

    id: int
    username: str
    real_name: str | None = None
    mobile: str | None = None
    email: str | None = None
    dept_id: int | None = None
    position: str | None = None
    status: int = 1
    role_ids: list[int] = Field(default_factory=list)        # 当前用户拥有的角色 ID 列表
    last_login_at: datetime | None = None                    # 最后登录时间
    created_at: datetime | None = None                       # 创建时间
    updated_at: datetime | None = None                       # 最后修改时间


class UserQuery(PageQuery):
    """用户列表查询参数。

    继承 PageQuery，自动带分页字段：
      - page：页码，默认 1
      - page_size：每页条数，默认 20，上限 100
      - keyword：模糊搜索关键词
    此外还增加了用户管理专用的筛选字段：
      - status：按启用/禁用筛选
      - dept_id：按部门筛选
    """

    status: int | None = None
    dept_id: int | None = None


class PasswordResetIn(BaseModel):
    """重置密码请求体。

    用于 POST /api/v1/sys/users/{user_id}/reset-password。
    password 可选：如果前端不传，后端会使用系统默认初始密码（如 "123456"）。
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    password: str | None = Field(default=None, min_length=6, max_length=128)
