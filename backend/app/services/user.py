"""用户业务逻辑。"""

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


DEFAULT_RESET_PASSWORD = "123456"
PROTECTED_USERNAME = "admin"
OPER_MODULE = "系统管理"


class UserService:
    """用户 CRUD 与密码重置。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.oper_logs = OperLogRepository(db)

    async def list_users(self, query: UserQuery) -> PageResult[UserOut]:
        rows, total = await self.users.list_users(query)
        role_map = await self.users.list_role_ids_map([row.id for row in rows])
        items = [
            UserOut.model_validate(row).model_copy(
                update={"role_ids": role_map.get(row.id, [])}
            )
            for row in rows
        ]
        return paginate_result(items, total, query.page, query.page_size)

    async def get_user(self, user_id: int) -> UserOut:
        user = await self._get_active_user(user_id)
        role_ids = await self.users.list_role_ids(user.id)
        return UserOut.model_validate(user).model_copy(update={"role_ids": role_ids})

    async def create_user(self, payload: UserCreate, current_user_id: int) -> UserOut:
        if await self.users.get_by_username(payload.username):
            raise AppException(ErrorCode.CONFLICT, "用户名已存在")
        await self._validate_dept(payload.dept_id)
        await self._validate_roles(payload.role_ids)
        user = SysUser(
            username=payload.username,
            password_hash=hash_password(payload.password),
            real_name=payload.real_name,
            mobile=payload.mobile,
            email=payload.email,
            dept_id=payload.dept_id,
            position=payload.position,
            status=payload.status,
        )
        await self.users.add(user)
        if payload.role_ids:
            await self.users.replace_roles(user.id, payload.role_ids)
        await self._write_log(current_user_id, "新增用户", user.id)
        await self.db.commit()
        return UserOut.model_validate(user).model_copy(
            update={"role_ids": list(payload.role_ids)}
        )

    async def update_user(
        self, user_id: int, payload: UserUpdate, current_user_id: int
    ) -> UserOut:
        user = await self._get_active_user(user_id)
        data = payload.model_dump(exclude_unset=True)
        role_ids = data.pop("role_ids", None)
        password = data.pop("password", None)
        status = data.get("status")
        if status is not None and status != user.status:
            self._assert_not_protected(user, current_user_id, "禁用或启用")
            # 状态变更使已签发的 Token 立即失效
            user.token_version += 1
        await self._validate_dept(data.get("dept_id"))
        await self._validate_roles(role_ids)
        if password:
            user.password_hash = hash_password(password)
            # 改密后旧 Token 失效
            user.token_version += 1
        for field, value in data.items():
            setattr(user, field, value)
        if role_ids is not None:
            await self.users.replace_roles(user_id, role_ids)
        await self._write_log(current_user_id, "修改用户", user_id)
        await self.db.commit()
        await self.db.refresh(user)
        new_role_ids = role_ids if role_ids is not None else await self.users.list_role_ids(user_id)
        return UserOut.model_validate(user).model_copy(
            update={"role_ids": list(new_role_ids)}
        )

    async def delete_user(self, user_id: int, current_user_id: int) -> None:
        user = await self._get_active_user(user_id)
        self._assert_not_protected(user, current_user_id, "删除")
        # 软删除：保留历史单据的 created_by 可追溯
        user.is_deleted = True
        await self._write_log(current_user_id, "删除用户", user_id)
        await self.db.commit()

    async def reset_password(
        self, user_id: int, password: str | None = None, current_user_id: int | None = None
    ) -> None:
        user = await self._get_active_user(user_id)
        user.password_hash = hash_password(password or DEFAULT_RESET_PASSWORD)
        # 重置密码后旧 Token 全部失效
        user.token_version += 1
        await self._write_log(current_user_id, "重置密码", user_id)
        await self.db.commit()

    async def _get_active_user(self, user_id: int) -> SysUser:
        user = await self.users.get(user_id)
        if user is None or user.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "用户不存在")
        return user

    async def _write_log(
        self, operator_id: int | None, action: str, target_id: int, detail: str = "success"
    ) -> None:
        """记录关键操作日志（与业务同事务提交）。"""
        await self.oper_logs.add(
            SysOperLog(
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
        if dept_id is None:
            return
        if dept_id not in await self.users.existing_dept_ids([dept_id]):
            raise AppException(ErrorCode.VALIDATION_ERROR, f"部门不存在: {dept_id}")

    async def _validate_roles(self, role_ids: list[int] | None) -> None:
        if not role_ids:
            return
        missing = sorted(set(role_ids) - await self.users.existing_role_ids(role_ids))
        if missing:
            raise AppException(
                ErrorCode.VALIDATION_ERROR, f"角色不存在: {missing}", data=missing
            )
