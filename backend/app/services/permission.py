"""权限业务逻辑：权限树查询与角色权限分配。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ErrorCode
from app.models.sys import SysPermission, SysRole
from app.repositories.permission import PermissionRepository
from app.schemas.permission import PermissionNodeOut


class PermissionService:
    """权限树与角色权限分配。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.permissions = PermissionRepository(db)

    async def get_tree(self) -> list[PermissionNodeOut]:
        """完整权限树（目录/菜单/按钮），供角色分配界面使用。"""
        nodes = await self.permissions.list_all()
        return self._build_tree(nodes)

    async def assign_role_permissions(self, role_id: int, permission_ids: list[int]) -> None:
        """全量覆盖角色权限：校验角色与权限 id 有效后重建关联。"""
        role = await self.db.get(SysRole, role_id)
        if role is None or role.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "角色不存在")
        if permission_ids:
            existing = await self.permissions.list_existing_ids(permission_ids)
            invalid = [pid for pid in permission_ids if pid not in existing]
            if invalid:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR, f"包含无效权限 id: {sorted(set(invalid))}"
                )
        await self.permissions.replace_role_permissions(role_id, permission_ids)
        await self.db.commit()

    def _build_tree(self, nodes: list[SysPermission]) -> list[PermissionNodeOut]:
        """将权限节点按 sort 组装为树，父节点不在集合内的挂到根。"""
        node_ids = {node.id for node in nodes}
        children_map: dict[int, list[SysPermission]] = {}
        roots: list[SysPermission] = []
        for node in nodes:
            parent_id = node.parent_id if node.parent_id in node_ids else None
            if parent_id is None:
                roots.append(node)
            else:
                children_map.setdefault(parent_id, []).append(node)

        def _build(parent: SysPermission | None) -> list[PermissionNodeOut]:
            siblings = roots if parent is None else children_map.get(parent.id, [])
            return [
                PermissionNodeOut(
                    id=node.id,
                    parent_id=node.parent_id if node.parent_id in node_ids else None,
                    name=node.name,
                    type=node.type,
                    perm_code=node.perm_code,
                    route_path=node.route_path,
                    component=node.component,
                    icon=node.icon,
                    sort=node.sort,
                    children=_build(node),
                )
                for node in sorted(siblings, key=lambda item: (item.sort, item.id))
            ]

        return _build(None)
