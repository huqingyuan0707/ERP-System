"""部门业务逻辑（B-06）：树 CRUD、排序、防环、引用删除保护与操作日志。

删除时依次检查子部门、员工、系统用户、角色数据范围引用，任一存在即返回 409。
停用部门只改变新档案的可选范围，已有员工与历史单据不受影响。
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ErrorCode
from app.models.sys import SysDept
from app.repositories.dept import DeptRepository
from app.schemas.dept import DeptCreate, DeptOut, DeptSortPayload, DeptUpdate
from app.services.audit import write_oper_log


OPER_MODULE = "基础资料"


class DeptService:
    """部门查询与写操作；commit=False 时由端点的幂等包装统一提交。"""

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.depts = DeptRepository(db, tenant_id=tenant_id)

    async def list_all(self) -> list[SysDept]:
        """全量有效部门（按 sort、id 排序），供 /sys/depts 等只读场景使用。"""
        return await self.depts.list_all()

    async def list_depts(self) -> list[DeptOut]:
        """部门树扁平列表，附带引用计数；前端按 parentId 组装树。"""
        rows = await self.depts.list_all()
        counts = await self.depts.reference_counts()
        return [self._to_out(row, counts.get(row.id, (0, 0, 0))) for row in rows]

    async def get_dept(self, dept_id: int) -> DeptOut:
        dept = await self._get_active(dept_id)
        refs = await self.depts.count_references(dept.id)
        return self._to_out(dept, (refs["children"], refs["employees"], refs["users"]))

    async def create_dept(
        self, payload: DeptCreate, operator_id: int, *, commit: bool = True
    ) -> DeptOut:
        """新增部门；名称同租户唯一，上级部门必须有效且不能形成环。"""
        try:
            await self.depts.lock_tree()
            name = payload.name.strip()
            if not name:
                raise AppException(ErrorCode.VALIDATION_ERROR, "部门名称不能为空")
            if await self.depts.get_by_name(name):
                raise AppException(ErrorCode.CONFLICT, f"部门名称已存在: {name}")
            if payload.parent_id is not None:
                await self._ensure_parent(payload.parent_id, None)
            dept = SysDept(
                name=name,
                parent_id=payload.parent_id,
                leader=payload.leader,
                sort=payload.sort,
                status=payload.status,
                remark=payload.remark,
                tenant_id=self.tenant_id,
                created_by=operator_id,
                updated_by=operator_id,
            )
            await self.depts.add(dept)
            await self._write_log(operator_id, "新增部门", dept.id, {"name": name})
            result = await self.get_dept(dept.id)
            if commit:
                await self.db.commit()
            return result
        except Exception:
            if commit:
                await self.db.rollback()
            raise

    async def update_dept(
        self, dept_id: int, payload: DeptUpdate, operator_id: int, *, commit: bool = True
    ) -> DeptOut:
        """部分更新部门；支持改名、换父级、改排序、启停。"""
        try:
            await self.depts.lock_tree()
            dept = await self._get_active(dept_id, for_update=True)
            data: dict[str, Any] = payload.model_dump(exclude_unset=True)
            if "name" in data:
                name = str(data["name"]).strip()
                if not name:
                    raise AppException(ErrorCode.VALIDATION_ERROR, "部门名称不能为空")
                if await self.depts.get_by_name(name, exclude_id=dept_id):
                    raise AppException(ErrorCode.CONFLICT, f"部门名称已存在: {name}")
                data["name"] = name
            parent_id = data.get("parent_id", dept.parent_id)
            if parent_id is not None:
                await self._ensure_parent(parent_id, dept_id)
            action = "修改部门"
            if "status" in data and data["status"] != dept.status:
                action = "启用部门" if data["status"] == 1 else "停用部门"
            for field, value in data.items():
                setattr(dept, field, value)
            dept.updated_by = operator_id
            await self._write_log(operator_id, action, dept.id)
            result = await self.get_dept(dept_id)
            if commit:
                await self.db.commit()
            return result
        except Exception:
            if commit:
                await self.db.rollback()
            raise

    async def reorder_depts(
        self, payload: DeptSortPayload, operator_id: int, *, commit: bool = True
    ) -> None:
        """批量调整排序；所有部门在同一事务内生效，避免上移/下移只成功一半。"""
        try:
            await self.depts.lock_tree()
            for item in payload.items:
                dept = await self._get_active(item.id, for_update=True)
                dept.sort = item.sort
                dept.updated_by = operator_id
            await self._write_log(
                operator_id,
                "调整部门排序",
                payload.items[0].id,
                {"items": [item.model_dump() for item in payload.items]},
            )
            if commit:
                await self.db.commit()
        except Exception:
            if commit:
                await self.db.rollback()
            raise

    async def delete_dept(
        self, dept_id: int, operator_id: int, *, commit: bool = True
    ) -> None:
        """软删除部门；存在子部门/员工/用户/角色数据范围引用时抛 409。"""
        try:
            await self.depts.lock_tree()
            dept = await self._get_active(dept_id, for_update=True)
            refs = await self.depts.count_references(dept_id)
            if refs["children"]:
                raise AppException(ErrorCode.CONFLICT, "该部门下存在子部门，无法删除")
            if refs["employees"]:
                raise AppException(ErrorCode.CONFLICT, "该部门下存在员工，无法删除")
            if refs["users"]:
                raise AppException(ErrorCode.CONFLICT, "该部门下存在系统用户，无法删除")
            if refs["role_dept"]:
                raise AppException(ErrorCode.CONFLICT, "该部门已被角色数据范围引用，无法删除")
            dept.is_deleted = True
            dept.updated_by = operator_id
            await self._write_log(operator_id, "删除部门", dept_id)
            if commit:
                await self.db.commit()
        except Exception:
            if commit:
                await self.db.rollback()
            raise

    async def _ensure_parent(self, parent_id: int, self_id: int | None) -> None:
        """校验上级部门存在且不是自身/自身下级，防止形成环。"""
        visited: set[int] = set()
        current_id: int | None = parent_id
        while current_id is not None:
            if current_id == self_id or current_id in visited:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR, "上级部门不能是自身、其下级或循环部门"
                )
            visited.add(current_id)
            parent = await self.depts.get_for_update(current_id)
            if parent is None or parent.is_deleted:
                raise AppException(ErrorCode.VALIDATION_ERROR, "上级部门不存在")
            current_id = parent.parent_id

    async def _get_active(self, dept_id: int, *, for_update: bool = False) -> SysDept:
        dept = await (
            self.depts.get_for_update(dept_id)
            if for_update
            else self.depts.get(dept_id)
        )
        if dept is None or dept.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "部门不存在")
        return dept

    @staticmethod
    def _to_out(dept: SysDept, counts: tuple[int, int, int]) -> DeptOut:
        return DeptOut.model_validate(dept).model_copy(
            update={
                "children_count": counts[0],
                "employee_count": counts[1],
                "user_count": counts[2],
            }
        )

    async def _write_log(
        self,
        operator_id: int,
        action: str,
        dept_id: int,
        extra: dict[str, Any] | None = None,
    ) -> None:
        params: dict[str, Any] = {"deptId": dept_id}
        if extra:
            params.update(extra)
        await write_oper_log(
            self.db,
            tenant_id=self.tenant_id,
            operator_id=operator_id,
            action=action,
            params=params,
            module=OPER_MODULE,
        )
