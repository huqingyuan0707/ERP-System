"""数据范围解析（C-04）。

``sys_role.data_scope``：``1=本人``（created_by=当前用户）、``2=本部门``
（created_by 所属部门在用户部门子树内；单据有 dept_id 列时优先用 dept_id）、
``3=全部``。多角色取最宽范围；管理员角色（code=admin）固定全部。
列表、count、详情、导出、报表共用本模块产出的过滤条件。
"""

from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.models.sys import SysDept, SysRole, SysRoleDept, SysUser, SysUserRole


SCOPE_SELF = 1
SCOPE_DEPT = 2
SCOPE_ALL = 3


@dataclass
class ScopeContext:
    """当前用户的数据范围上下文。"""

    scope: int
    user_id: int
    dept_id: int | None = None
    # 数据范围 2 时角色配置的自定义部门集（sys_role_dept），与用户部门子树取并集
    extra_dept_ids: list[int] = field(default_factory=list)


async def resolve_scope(db: AsyncSession, user: SysUser) -> ScopeContext:
    """解析用户数据范围：多角色取最宽；admin 固定全部。"""
    roles = (
        (
            await db.execute(
                select(SysRole)
                .join(SysUserRole, SysUserRole.role_id == SysRole.id)
                .where(SysUserRole.user_id == user.id, SysRole.is_deleted.is_(False))
            )
        )
        .scalars()
        .all()
    )

    scope = SCOPE_SELF
    for role in roles:
        if role.code == "admin":
            return ScopeContext(scope=SCOPE_ALL, user_id=user.id, dept_id=user.dept_id)
        scope = max(scope, role.data_scope)

    extra: list[int] = []
    if scope == SCOPE_DEPT and roles:
        extra = list(
            (
                await db.execute(
                    select(SysRoleDept.dept_id).where(
                        SysRoleDept.role_id.in_([r.id for r in roles])
                    )
                )
            )
            .scalars()
            .all()
        )
    return ScopeContext(scope=scope, user_id=user.id, dept_id=user.dept_id, extra_dept_ids=extra)


async def _dept_subtree_ids(db: AsyncSession, dept_id: int | None) -> set[int]:
    """部门及全部子孙部门 id（递归 CTE）。"""
    if dept_id is None:
        return set()
    subtree = select(SysDept.id).where(
        SysDept.id == dept_id, SysDept.is_deleted.is_(False)
    ).cte(recursive=True)
    subtree = subtree.union_all(
        select(SysDept.id)
        .join(subtree, SysDept.parent_id == subtree.c.id)
        .where(SysDept.is_deleted.is_(False))
    )
    return set((await db.execute(select(subtree.c.id))).scalars().all())


async def _visible_dept_ids(db: AsyncSession, ctx: ScopeContext) -> set[int]:
    """数据范围 2 的可见部门集合：用户部门子树 ∪ 角色自定义部门。"""
    subtree = await _dept_subtree_ids(db, ctx.dept_id)
    return subtree | set(ctx.extra_dept_ids)


async def apply_scope(
    db: AsyncSession,
    stmt: Select,
    model: type,
    ctx: ScopeContext,
    dept_id_column: object | None = None,
) -> Select:
    """为查询语句追加数据范围过滤；列表、count、导出、报表共用。

    ``model`` 需含 ``created_by`` 列；``dept_id_column`` 为单据部门列
    （存在时数据范围 2 优先按部门列过滤）。
    """
    if ctx.scope == SCOPE_ALL:
        return stmt
    if ctx.scope == SCOPE_SELF:
        return stmt.where(model.created_by == ctx.user_id)

    dept_ids = await _visible_dept_ids(db, ctx)
    if dept_id_column is not None:
        conditions = [model.created_by == ctx.user_id]
        if dept_ids:
            conditions.append(dept_id_column.in_(dept_ids))
        return stmt.where(or_(*conditions))

    subtree_users = set(
        (
            await db.execute(
                select(SysUser.id).where(
                    SysUser.dept_id.in_(dept_ids or {0}), SysUser.is_deleted.is_(False)
                )
            )
        )
        .scalars()
        .all()
    )
    subtree_users.add(ctx.user_id)
    return stmt.where(model.created_by.in_(subtree_users))


async def allows(
    db: AsyncSession,
    ctx: ScopeContext,
    *,
    created_by: int | None,
    dept_id: int | None = None,
) -> bool:
    """详情/写操作越权判定：不在范围内返回 False（调用方抛 403）。"""
    if ctx.scope == SCOPE_ALL:
        return True
    if created_by == ctx.user_id:
        return True
    if ctx.scope == SCOPE_SELF:
        return False
    dept_ids = await _visible_dept_ids(db, ctx)
    if dept_id is not None and dept_id in dept_ids:
        return True
    if dept_id is None and created_by is not None:
        creator_dept = await db.scalar(select(SysUser.dept_id).where(SysUser.id == created_by))
        return creator_dept is not None and creator_dept in dept_ids
    return False
