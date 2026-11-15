"""员工接口（B-06）：/base/employees 的 CRUD、可选员工与可关联用户。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.employee import (
    EmployeeCreate,
    EmployeeOut,
    EmployeeQuery,
    EmployeeUpdate,
    UserOptionOut,
)
from app.services.employee import EmployeeService
from app.services.idempotency import IdempotencyService


router = APIRouter(prefix="/base", tags=["基础资料-员工"])

LIST_PERM = "base:employee:list"


@router.get("/employees", summary="员工列表")
async def list_employees(
    query: Annotated[EmployeeQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[PageResult[EmployeeOut]]:
    return success_response(await EmployeeService(db, tenant_id).list_employees(query))


# 静态路径必须先于 /employees/{employee_id} 注册
@router.get("/employees/options", summary="新单据可选员工（仅启用）")
async def list_selectable_employees(
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[list[EmployeeOut]]:
    return success_response(await EmployeeService(db, tenant_id).list_selectable())


@router.get("/employees/user-options", summary="可关联的系统用户")
async def list_linkable_users(
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[list[UserOptionOut]]:
    return success_response(await EmployeeService(db, tenant_id).list_linkable_users())


@router.post("/employees", summary="新增员工")
async def create_employee(
    payload: EmployeeCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:employee:add"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[EmployeeOut]:
    service = EmployeeService(db, tenant_id)

    async def operation():
        return await service.create_employee(payload, current_user.id, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="create_employee",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(EmployeeOut.model_validate(result))


@router.get("/employees/{employee_id}", summary="员工详情")
async def get_employee(
    employee_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[EmployeeOut]:
    return success_response(await EmployeeService(db, tenant_id).get_employee(employee_id))


@router.put("/employees/{employee_id}", summary="修改员工")
async def update_employee(
    employee_id: int,
    payload: EmployeeUpdate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:employee:edit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[EmployeeOut]:
    service = EmployeeService(db, tenant_id)

    async def operation():
        return await service.update_employee(
            employee_id, payload, current_user.id, commit=False
        )

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="update_employee",
        idempotency_key=idempotency_key,
        request_body={
            "id": employee_id,
            "payload": payload.model_dump(mode="json", exclude_unset=True),
        },
        operation=operation,
    )
    return success_response(EmployeeOut.model_validate(result))


@router.delete("/employees/{employee_id}", summary="删除员工")
async def delete_employee(
    employee_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:employee:delete"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[None]:
    service = EmployeeService(db, tenant_id)

    async def operation():
        return await service.delete_employee(employee_id, current_user.id, commit=False)

    await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="delete_employee",
        idempotency_key=idempotency_key,
        request_body={"id": employee_id},
        operation=operation,
    )
    return success_response(None)
