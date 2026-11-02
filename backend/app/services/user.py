"""用户业务逻辑。

本文件是 Service 层，承担用户管理的核心业务规则：
  - 数据校验（账号唯一、部门/角色是否存在、密码强度等）；
  - 敏感数据处理（密码用 bcrypt 哈希，明文不进数据库）；
  - 状态机与并发安全（token_version 令牌版本号、版本号校验等）；
  - 操作日志（所有增删改操作都要记录，与业务在同一个事务里提交）。

层级关系：
  Router（接口层，system.py）
    -> Service（业务层，本文件）
    -> Repository（数据访问层，repositories/user.py）
"""

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ErrorCode
from app.core.security import hash_password
from app.models.sys import SysOperLog, SysUser
from app.repositories.oper_log import OperLogRepository
from app.repositories.user import UserRepository
from app.schemas.common import PageResult
from app.schemas.user import UserCreate, UserOut, UserQuery, UserUpdate
from app.utils.pagination import paginate_result


# 系统默认初始密码：重置密码时若前端未提供，则使用这个值
DEFAULT_RESET_PASSWORD = "123456"
# 受保护账号：内置超级管理员，禁止被删除或禁用
PROTECTED_USERNAME = "admin"
# 操作日志的模块名称，写入 sys_oper_log 的 module 字段
OPER_MODULE = "系统管理"


class UserService:
    """用户 CRUD 与密码重置业务。"""

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.users = UserRepository(db, tenant_id=tenant_id)  # 用户数据访问对象（已按租户隔离）
        self.oper_logs = OperLogRepository(db)                # 操作日志数据访问对象

    # ------------------------------------------------------------------
    # 查询类
    # ------------------------------------------------------------------

    async def list_users(self, query: UserQuery) -> PageResult[UserOut]:
        """用户列表查询（分页 + 模糊搜索 + 部门/状态筛选）。

        流程：
          1. 调用 Repository 查出当前页的用户数据和总数；
          2. 批量查询这些用户的角色 ID（避免在循环里逐个查，造成 N+1 问题）；
          3. 把 ORM 模型转成 Pydantic 输出模型 UserOut，并填入 role_ids；
          4. 封装成分页结果返回。
        """
        rows, total = await self.users.list_users(query)
        # 批量查所有用户的角色映射，一次 SQL 搞定，性能更好
        role_map = await self.users.list_role_ids_map([row.id for row in rows])
        items = [
            UserOut.model_validate(row).model_copy(
                update={"role_ids": role_map.get(row.id, [])}
            )
            for row in rows
        ]
        return paginate_result(items, total, query.page, query.page_size)

    async def get_user(self, user_id: int) -> UserOut:
        """用户详情查询。

        流程：
          1. 根据 user_id 查用户，不存在或已被软删除则抛出业务异常；
          2. 查出该用户关联的角色 ID 列表；
          3. 转成 UserOut 返回。
        """
        user = await self._get_active_user(user_id)
        role_ids = await self.users.list_role_ids(user.id)
        return UserOut.model_validate(user).model_copy(update={"role_ids": role_ids})

    # ------------------------------------------------------------------
    # 写操作类（新增/修改/删除/重置密码）
    # ------------------------------------------------------------------

    async def create_user(self, payload: UserCreate, current_user_id: int) -> UserOut:
        """新增用户。

        业务规则：
          1. 账号必须唯一，已存在则拒绝；
          2. 所属部门必须存在；
          3. 关联的角色必须存在；
          4. 密码用 bcrypt 哈希后存储，明文不进数据库；
          5. 用户和角色多对多关系，写入 sys_user_role 关联表；
          6. 写操作日志，与用户创建在同一个事务里提交；
          7. 提交后返回新用户信息（不含密码）。
        """
        if await self.users.get_by_username(payload.username):
            raise AppException(ErrorCode.CONFLICT, "用户名已存在")
        await self._validate_dept(payload.dept_id)
        await self._validate_roles(payload.role_ids)
        user = SysUser(
            username=payload.username,
            password_hash=hash_password(payload.password),  # 关键：只存哈希，不存明文
            real_name=payload.real_name,
            mobile=payload.mobile,
            email=payload.email,
            dept_id=payload.dept_id,
            position=payload.position,
            status=payload.status,
        )
        await self.users.add(user)
        if payload.role_ids:
            await self.users.replace_roles(user.id, payload.role_ids)  # 写用户-角色关联
        await self._write_log(current_user_id, "新增用户", user.id)
        await self.db.commit()  # 提交事务，用户、角色关联、操作日志一起生效或一起回滚
        return UserOut.model_validate(user).model_copy(
            update={"role_ids": list(payload.role_ids)}
        )

    async def update_user(
        self, user_id: int, payload: UserUpdate, current_user_id: int
    ) -> UserOut:
        """修改用户（部分更新）。

        业务规则：
          1. 用户必须存在且未被删除；
          2. 只更新前端传了的字段（model_dump(exclude_unset=True)）；
          3. 修改状态（启用/禁用）或密码时，token_version +1，使已签发的旧 Token 立即失效；
          4. 修改状态或密码时，不能操作自己和内置 admin 账号；
          5. 角色变更时，重建 sys_user_role 关联（先删后插，在事务里完成）；
          6. 写操作日志，提交事务。
        """
        user = await self._get_active_user(user_id)
        data = payload.model_dump(exclude_unset=True)  # 只取前端实际传了的字段
        role_ids = data.pop("role_ids", None)
        password = data.pop("password", None)
        status = data.get("status")

        # 状态变更（启用<->禁用）时做保护校验，并让旧 Token 失效
        if status is not None and status != user.status:
            self._assert_not_protected(user, current_user_id, "禁用或启用")
            user.token_version += 1  # 令牌版本号递增，旧 JWT 在下次请求时会被拒绝

        await self._validate_dept(data.get("dept_id"))
        await self._validate_roles(role_ids)

        if password:
            # 规则 4 的另一半：修改密码同样禁止操作自己或内置 admin，
            # 否则管理员可能误改自身凭证被强制登出，甚至锁死内置账号
            self._assert_not_protected(user, current_user_id, "修改")
            user.password_hash = hash_password(password)
            user.token_version += 1  # 改密后旧 Token 全部失效，强制用户重新登录

        # 把剩余字段（real_name、mobile、email、dept_id、position 等）批量写入模型
        for field, value in data.items():
            setattr(user, field, value)

        if role_ids is not None:
            await self.users.replace_roles(user_id, role_ids)

        await self._write_log(current_user_id, "修改用户", user_id)
        await self.db.commit()
        await self.db.refresh(user)  # 从数据库刷新最新状态（如自增主键、默认值等）
        new_role_ids = (
            role_ids if role_ids is not None else await self.users.list_role_ids(user_id)
        )
        return UserOut.model_validate(user).model_copy(
            update={"role_ids": list(new_role_ids)}
        )

    async def delete_user(self, user_id: int, current_user_id: int) -> None:
        """删除用户（逻辑删除，软删）。

        业务规则：
          1. 用户必须存在且未被删除；
          2. 不能删除自己，不能删除内置 admin 账号；
          3. 不是物理删除，只是把 is_deleted 设为 True，保留历史数据；
             这样之前由该用户创建的采购单、销售单等历史单据，created_by 还能查到姓名；
          4. 写操作日志，提交事务。
        """
        user = await self._get_active_user(user_id)
        self._assert_not_protected(user, current_user_id, "删除")
        user.is_deleted = True  # 软删除标记
        await self._write_log(current_user_id, "删除用户", user_id)
        await self.db.commit()

    async def reset_password(
        self, user_id: int, password: str | None = None, current_user_id: int | None = None
    ) -> None:
        """重置密码。

        业务规则：
          1. 用户必须存在且未被删除；
          2. 前端如果不传新密码，使用系统默认初始密码（如 "123456"）；
          3. 密码用 bcrypt 哈希存储；
          4. 重置后令牌版本号 +1，使用该账号的所有旧 Token 立即失效，强制重新登录；
          5. 写操作日志，提交事务。
        """
        user = await self._get_active_user(user_id)
        user.password_hash = hash_password(password or DEFAULT_RESET_PASSWORD)
        user.token_version += 1  # 旧 Token 全失效
        await self._write_log(current_user_id, "重置密码", user_id)
        await self.db.commit()

    # ------------------------------------------------------------------
    # 私有辅助方法
    # ------------------------------------------------------------------

    async def _get_active_user(self, user_id: int) -> SysUser:
        """根据 ID 查用户，不存在或已软删除则抛出 404 业务异常。"""
        user = await self.users.get(user_id)
        if user is None or user.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "用户不存在")
        return user

    async def _write_log(
        self, operator_id: int | None, action: str, target_id: int, detail: str = "success"
    ) -> None:
        """记录关键操作日志（与业务同事务提交）。

        写入 sys_oper_log 表，记录：谁操作的、模块、动作、目标ID、结果。
        因为和业务在同一个事务里，业务回滚时日志也会一起回滚，保证一致性。
        """
        await self.oper_logs.add(
            SysOperLog(
                tenant_id=self.tenant_id,
                user_id=operator_id,
                module=OPER_MODULE,
                action=action,
                params=json.dumps({"userId": target_id}, ensure_ascii=False),
                result=detail,
                status=1,
            )
        )

    def _assert_not_protected(self, user: SysUser, current_user_id: int, action: str) -> None:
        """禁止删除/禁用当前登录用户与内置 admin 账号。"""
        if user.id == current_user_id:
            raise AppException(ErrorCode.BAD_REQUEST, f"不能{action}当前登录用户")
        if user.username == PROTECTED_USERNAME:
            raise AppException(ErrorCode.BAD_REQUEST, f"不能{action}内置管理员账号")

    async def _validate_dept(self, dept_id: int | None) -> None:
        """校验部门是否存在。"""
        if dept_id is None:
            return
        if dept_id not in await self.users.existing_dept_ids([dept_id]):
            raise AppException(ErrorCode.VALIDATION_ERROR, f"部门不存在: {dept_id}")

    async def _validate_roles(self, role_ids: list[int] | None) -> None:
        """校验角色是否存在。"""
        if not role_ids:
            return
        missing = sorted(set(role_ids) - await self.users.existing_role_ids(role_ids))
        if missing:
            raise AppException(
                ErrorCode.VALIDATION_ERROR, f"角色不存在: {missing}", data=missing
            )
