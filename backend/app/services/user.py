"""用户业务逻辑。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ErrorCode
from app.core.security import hash_password
from app.models.sys import SysUser
from app.repositories.user import UserRepository
from app.schemas.common import PageResult
from app.schemas.user import UserCreate, UserOut, UserQuery, UserUpdate
from app.utils.pagination import paginate_result


DEFAULT_RESET_PASSWORD = "123456"


class UserService:
    """用户 CRUD 与密码重置。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.users = UserRepository(db)

    async def list_users(self, query: UserQuery) -> PageResult[UserOut]:
        rows, total = await self.users.list_users(query)
        items = [UserOut.model_validate(row) for row in rows]
        return paginate_result(items, total, query.page, query.page_size)

    async def get_user(self, user_id: int) -> UserOut:
        user = await self._get_active_user(user_id)
        return UserOut.model_validate(user)

    async def create_user(self, payload: UserCreate) -> UserOut:
        if await self.users.get_by_username(payload.username):
            raise AppException(ErrorCode.CONFLICT, "用户名已存在")
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
        await self.db.commit()
        return UserOut.model_validate(user)

    async def update_user(self, user_id: int, payload: UserUpdate) -> UserOut:
        user = await self._get_active_user(user_id)
        data = payload.model_dump(exclude_unset=True)
        role_ids = data.pop("role_ids", None)
        password = data.pop("password", None)
        if password:
            user.password_hash = hash_password(password)
        for field, value in data.items():
            setattr(user, field, value)
        if role_ids is not None:
            await self.users.replace_roles(user_id, role_ids)
        await self.db.commit()
        await self.db.refresh(user)
        return UserOut.model_validate(user)

    async def delete_user(self, user_id: int) -> None:
        user = await self._get_active_user(user_id)
        user.is_deleted = True
        await self.db.commit()

    async def reset_password(self, user_id: int, password: str | None = None) -> None:
        user = await self._get_active_user(user_id)
        user.password_hash = hash_password(password or DEFAULT_RESET_PASSWORD)
        await self.db.commit()

    async def _get_active_user(self, user_id: int) -> SysUser:
        user = await self.users.get(user_id)
        if user is None or user.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "用户不存在")
        return user
