"""员工业务逻辑（B-06）：CRUD、编码唯一、关联系统用户、停用与操作日志。

数据闭环：bd_employee.dept_id + user_id -> sys_user.dept_id -> 数据范围/单据申请人。
关联或调整部门时同步 sys_user.dept_id/position；resolve_scope 每次请求从数据库
读取用户部门，因此无需递增 token_version，也不会把在线用户踢下线。
停用员工仅影响新单据可选列表，不改动账号状态、不触碰历史单据。
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ErrorCode
from app.models.base_data import BdEmployee
from app.models.sys import SysDept, SysUser
from app.repositories.dept import DeptRepository
from app.repositories.employee import EmployeeRepository
from app.schemas.common import PageResult
from app.schemas.employee import (
    EmployeeCreate,
    EmployeeOut,
    EmployeeQuery,
    EmployeeUpdate,
    UserOptionOut,
)
from app.services.audit import write_oper_log
from app.utils.code_gen import format_code, next_seq_from_code
from app.utils.pagination import paginate_result


EMPLOYEE_CODE_PREFIX = "EMP"
OPER_MODULE = "基础资料"


class EmployeeService:
    """员工查询与写操作；commit=False 时由端点的幂等包装统一提交。"""

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.employees = EmployeeRepository(db, tenant_id=tenant_id)
        self.depts = DeptRepository(db, tenant_id=tenant_id)

    async def list_employees(self, query: EmployeeQuery) -> PageResult[EmployeeOut]:
        """分页查询员工，支持按部门（可选含下级）与状态筛选。"""
        dept_ids: set[int] | None = None
        if query.dept_id is not None and query.include_sub_dept:
            dept_ids = await self.depts.subtree_ids(query.dept_id)
        stmt = self.employees.build_query(query, dept_ids)
        rows, total = await self.employees.paginate(stmt, query.page, query.page_size)
        items = await self._to_out_list(rows)
        return paginate_result(items, total, query.page, query.page_size)

    async def get_employee(self, employee_id: int) -> EmployeeOut:
        employee = await self._get_active(employee_id)
        return (await self._to_out_list([employee]))[0]

    async def list_selectable(self) -> list[EmployeeOut]:
        """新单据可选员工（仅启用）；停用员工不影响历史单据展示。"""
        return await self._to_out_list(await self.employees.list_selectable())

    async def list_linkable_users(self) -> list[UserOptionOut]:
        """启用系统用户列表，并标注是否已被其他员工关联。"""
        linked = await self.employees.linked_user_ids()
        stmt = (
            select(SysUser)
            .where(
                SysUser.tenant_id == self.tenant_id,
                SysUser.is_deleted.is_(False),
                SysUser.status == 1,
            )
            .order_by(SysUser.id)
        )
        users = list((await self.db.execute(stmt)).scalars().all())
        return [
            UserOptionOut(
                id=user.id,
                username=user.username,
                real_name=user.real_name,
                dept_id=user.dept_id,
                status=user.status,
                linked_employee_id=linked.get(user.id),
            )
            for user in users
        ]

    async def create_employee(
        self, payload: EmployeeCreate, operator_id: int, *, commit: bool = True
    ) -> EmployeeOut:
        """新增员工；编码唯一，部门与关联用户必须有效，并同步用户部门。"""
        try:
            await self.employees.lock_for_write()
            code = (payload.code or "").strip() or await self._next_code()
            name = payload.name.strip()
            if not name:
                raise AppException(ErrorCode.VALIDATION_ERROR, "员工姓名不能为空")
            if await self.employees.get_by_code(code):
                raise AppException(ErrorCode.CONFLICT, f"员工编码已存在: {code}")
            await self._validate_dept(payload.dept_id)
            user = await self._resolve_user(payload.user_id, None)
            employee = BdEmployee(
                code=code,
                name=name,
                dept_id=payload.dept_id,
                position=payload.position,
                mobile=payload.mobile,
                entry_date=payload.entry_date,
                status=payload.status,
                user_id=payload.user_id,
                remark=payload.remark,
                tenant_id=self.tenant_id,
                created_by=operator_id,
                updated_by=operator_id,
            )
            await self.employees.add(employee)
            if user is not None:
                self._sync_user(user, employee, operator_id)
            await self._write_log(
                operator_id, "新增员工", employee.id, {"code": code, "deptId": employee.dept_id}
            )
            if user is not None:
                await self._write_log(
                    operator_id,
                    "关联系统用户",
                    employee.id,
                    {"userId": user.id, "deptId": employee.dept_id},
                )
            result = await self.get_employee(employee.id)
            if commit:
                await self.db.commit()
            return result
        except Exception:
            if commit:
                await self.db.rollback()
            raise

    async def update_employee(
        self,
        employee_id: int,
        payload: EmployeeUpdate,
        operator_id: int,
        *,
        commit: bool = True,
    ) -> EmployeeOut:
        """部分更新员工；改部门/关联用户时同步 sys_user.dept_id，停用只影响新单据。"""
        try:
            await self.employees.lock_for_write()
            employee = await self._get_active(employee_id, for_update=True)
            data: dict[str, Any] = payload.model_dump(exclude_unset=True)
            for field in ("code", "name", "dept_id", "status"):
                if field in data and data[field] is None:
                    raise AppException(ErrorCode.VALIDATION_ERROR, f"{field} 不能为 null")
            if "code" in data:
                code = str(data["code"]).strip()
                if not code:
                    raise AppException(ErrorCode.VALIDATION_ERROR, "员工编码不能为空")
                if await self.employees.get_by_code(code, exclude_id=employee_id):
                    raise AppException(ErrorCode.CONFLICT, f"员工编码已存在: {code}")
                data["code"] = code
            if "name" in data:
                name = str(data["name"]).strip()
                if not name:
                    raise AppException(ErrorCode.VALIDATION_ERROR, "员工姓名不能为空")
                data["name"] = name
            if "dept_id" in data:
                await self._validate_dept(data["dept_id"])
            previous_user_id = employee.user_id
            if data.get("user_id") is not None and "user_id" in data:
                await self._resolve_user(data["user_id"], employee_id)

            action = "修改员工"
            if "status" in data and data["status"] != employee.status:
                action = "启用员工" if data["status"] == 1 else "停用员工"
            for field, value in data.items():
                setattr(employee, field, value)
            employee.updated_by = operator_id
            needs_sync = any(
                key in data for key in ("dept_id", "position", "user_id")
            )
            if employee.user_id is not None and needs_sync:
                linked = await self._load_user(employee.user_id)
                if linked is not None:
                    self._sync_user(linked, employee, operator_id)
            await self._write_log(operator_id, action, employee.id)
            if "user_id" in data and data["user_id"] != previous_user_id:
                await self._write_log(
                    operator_id,
                    "关联系统用户" if data["user_id"] is not None else "解除用户关联",
                    employee.id,
                    {
                        "previousUserId": previous_user_id,
                        "userId": data["user_id"],
                        "deptId": employee.dept_id,
                    },
                )
            result = await self.get_employee(employee_id)
            if commit:
                await self.db.commit()
            return result
        except Exception:
            if commit:
                await self.db.rollback()
            raise

    async def delete_employee(
        self, employee_id: int, operator_id: int, *, commit: bool = True
    ) -> None:
        """软删除员工；历史单据按申请人/编码关联，不受软删影响。"""
        try:
            await self.employees.lock_for_write()
            employee = await self._get_active(employee_id, for_update=True)
            employee.is_deleted = True
            employee.updated_by = operator_id
            await self._write_log(operator_id, "删除员工", employee.id)
            if commit:
                await self.db.commit()
        except Exception:
            if commit:
                await self.db.rollback()
            raise

    async def _validate_dept(self, dept_id: int) -> None:
        """新增/换部门时要求部门存在、同租户、未删除且启用。"""
        stmt = select(SysDept).where(
            SysDept.id == dept_id,
            SysDept.tenant_id == self.tenant_id,
            SysDept.is_deleted.is_(False),
        )
        dept = (await self.db.execute(stmt)).scalar_one_or_none()
        if dept is None:
            raise AppException(ErrorCode.VALIDATION_ERROR, "所属部门不存在")
        if dept.status != 1:
            raise AppException(ErrorCode.VALIDATION_ERROR, "所属部门已停用")

    async def _resolve_user(
        self, user_id: int | None, exclude_employee_id: int | None
    ) -> SysUser | None:
        """校验关联账号可用且未被其他员工占用。"""
        if user_id is None:
            return None
        user = await self._load_user(user_id)
        if user is None:
            raise AppException(ErrorCode.VALIDATION_ERROR, "关联的系统用户不存在")
        if user.status != 1:
            raise AppException(ErrorCode.VALIDATION_ERROR, "关联的系统用户已禁用")
        if await self.employees.get_by_user(user_id, exclude_employee_id):
            raise AppException(ErrorCode.CONFLICT, "该系统用户已关联其他员工")
        return user

    async def _load_user(self, user_id: int) -> SysUser | None:
        stmt = select(SysUser).where(
            SysUser.id == user_id,
            SysUser.tenant_id == self.tenant_id,
            SysUser.is_deleted.is_(False),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    def _sync_user(user: SysUser, employee: BdEmployee, operator_id: int) -> None:
        """把员工部门/岗位回写到系统用户，驱动「本部门」数据范围。"""
        user.dept_id = employee.dept_id
        user.position = employee.position
        user.updated_by = operator_id

    async def _next_code(self) -> str:
        max_code = await self.employees.max_code(EMPLOYEE_CODE_PREFIX)
        return format_code(
            EMPLOYEE_CODE_PREFIX, next_seq_from_code(max_code, EMPLOYEE_CODE_PREFIX)
        )

    async def _get_active(self, employee_id: int, *, for_update: bool = False) -> BdEmployee:
        employee = await (
            self.employees.get_for_update(employee_id)
            if for_update
            else self.employees.get(employee_id)
        )
        if employee is None or employee.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "员工不存在")
        return employee

    async def _to_out_list(self, rows: list[BdEmployee]) -> list[EmployeeOut]:
        """批量补部门名称与登录账号，避免列表逐条关联查询。"""
        if not rows:
            return []
        dept_ids = {row.dept_id for row in rows}
        user_ids = {row.user_id for row in rows if row.user_id is not None}
        dept_names: dict[int, str] = {}
        if dept_ids:
            result = await self.db.execute(
                select(SysDept.id, SysDept.name).where(
                    SysDept.id.in_(dept_ids), SysDept.tenant_id == self.tenant_id
                )
            )
            dept_names = {int(row[0]): str(row[1]) for row in result.all()}
        usernames: dict[int, str] = {}
        if user_ids:
            result = await self.db.execute(
                select(SysUser.id, SysUser.username).where(
                    SysUser.id.in_(user_ids), SysUser.tenant_id == self.tenant_id
                )
            )
            usernames = {int(row[0]): str(row[1]) for row in result.all()}
        return [
            EmployeeOut.model_validate(row).model_copy(
                update={
                    "dept_name": dept_names.get(row.dept_id),
                    "username": usernames.get(row.user_id) if row.user_id else None,
                }
            )
            for row in rows
        ]

    async def _write_log(
        self,
        operator_id: int,
        action: str,
        employee_id: int,
        extra: dict[str, Any] | None = None,
    ) -> None:
        params: dict[str, Any] = {"employeeId": employee_id}
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
