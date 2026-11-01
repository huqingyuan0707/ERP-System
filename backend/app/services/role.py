"""角色服务：CRUD 与数据范围分配。"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ErrorCode
from app.models.sys import SysRole
from app.repositories.role import RoleRepository
from app.schemas.role import DataScopeAssign, RoleCreate, RoleUpdate


class RoleService:
    """角色业务逻辑。"""

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self._db = db
        self.tenant_id = tenant_id
        self._repo = RoleRepository(db, tenant_id=tenant_id)  # 角色数据访问对象（已按租户隔离）

    async def list_roles(self) -> list[dict[str, object]]:
        """角色列表（附带已配置的数据权限部门）。"""
        roles = await self._repo.list_all()
        result: list[dict[str, object]] = []
        for role in roles:
            out = {
                "id": role.id,
                "code": role.code,
                "name": role.name,
                "data_scope": role.data_scope,
                "status": role.status,
                "remark": role.remark,
                "dept_ids": await self._repo.list_dept_ids(role.id),
            }
            result.append(out)
        return result

    async def create_role(self, payload: RoleCreate) -> SysRole:
        """新增角色。"""
        if await self._repo.get_by_code(payload.code) is not None:
            raise AppException(ErrorCode.CONFLICT, f"角色编码 {payload.code} 已存在")
        entity = SysRole(
            code=payload.code,
            name=payload.name,
            data_scope=payload.dataScope,
            remark=payload.remark,
        )
        self._db.add(entity)
        try:
            await self._db.commit()
        except IntegrityError as exc:
            await self._db.rollback()
            raise AppException(ErrorCode.CONFLICT, f"角色编码 {payload.code} 已存在") from exc
        return entity

    async def update_role(self, role_id: int, payload: RoleUpdate) -> SysRole:
        """修改角色（编码与数据范围不在此接口修改）。"""
        entity = await self._get_role(role_id)
        for field in ("name", "remark"):
            value = getattr(payload, field)
            if value is not None:
                setattr(entity, field, value)
        await self._db.commit()
        return entity

    async def delete_role(self, role_id: int) -> None:
        """删除角色（软删）；内置 admin 不可删，已绑定用户或已分配权限不可删。"""
        entity = await self._get_role(role_id)
        if entity.code == "admin":
            raise AppException(ErrorCode.BAD_REQUEST, "内置管理员角色不可删除")
        if await self._repo.count_user_roles(role_id) > 0:
            raise AppException(ErrorCode.CONFLICT, "角色已绑定用户，请先解绑")
        if await self._repo.count_permissions(role_id) > 0:
            raise AppException(ErrorCode.CONFLICT, "角色已分配权限，请先清空权限")
        entity.is_deleted = True
        await self._db.commit()

    async def assign_data_scope(self, role_id: int, payload: DataScopeAssign) -> None:
        """分配数据范围；范围 2=本部门 时校验部门存在。"""
        entity = await self._get_role(role_id)
        entity.data_scope = payload.dataScope
        if payload.dataScope == 2 and payload.deptIds:
            from sqlalchemy import select

            from app.models.sys import SysDept

            stmt = select(SysDept.id).where(
                SysDept.id.in_(payload.deptIds), SysDept.is_deleted.is_(False)
            )
            found = set((await self._db.execute(stmt)).scalars().all())
            missing = [d for d in payload.deptIds if d not in found]
            if missing:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR, f"包含无效部门 id: {missing}"
                )
        await self._repo.replace_dept_ids(role_id, payload.deptIds)
        await self._db.commit()

    async def _get_role(self, role_id: int) -> SysRole:
        entity = await self._repo.get_by_id(role_id)
        if entity is None:
            raise AppException(ErrorCode.NOT_FOUND, "角色不存在")
        return entity
