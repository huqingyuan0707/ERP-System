"""部门接口（B-06）：/base/depts 的树 CRUD、批量排序与引用删除保护。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, success_response
from app.schemas.dept import DeptCreate, DeptOut, DeptSortPayload, DeptUpdate
from app.services.dept import DeptService
from app.services.idempotency import IdempotencyService

router = APIRouter(prefix="/base", tags=["基础资料-部门"])


@router.get("/depts", summary="部门树列表")
async def list_depts(
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:dept:list"))],
) -> ApiResponse[list[DeptOut]]:
    return success_response(await DeptService(db, tenant_id).list_depts())


@router.post("/depts", summary="新增部门")
async def create_dept(
    payload: DeptCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:dept:add"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[DeptOut]:
    service = DeptService(db, tenant_id)

    async def operation():
        return await service.create_dept(payload, current_user.id, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="create_dept",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(DeptOut.model_validate(result))


# 静态路径必须注册在 /depts/{dept_id} 之前，否则 "sort" 会被当成部门 id 解析（422）
@router.put("/depts/sort", summary="批量调整部门排序")
async def reorder_depts(
    payload: DeptSortPayload,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:dept:edit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[None]:
    service = DeptService(db, tenant_id)

    async def operation():
        return await service.reorder_depts(payload, current_user.id, commit=False)

    await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="sort_depts",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(None)


@router.get("/depts/{dept_id}", summary="部门详情")
async def get_dept(
    dept_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:dept:list"))],
) -> ApiResponse[DeptOut]:
    return success_response(await DeptService(db, tenant_id).get_dept(dept_id))


@router.put("/depts/{dept_id}", summary="修改部门")
async def update_dept(
    dept_id: int,
    payload: DeptUpdate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:dept:edit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[DeptOut]:
    service = DeptService(db, tenant_id)

    async def operation():
        return await service.update_dept(dept_id, payload, current_user.id, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="update_dept",
        idempotency_key=idempotency_key,
        request_body={
            "id": dept_id,
            "payload": payload.model_dump(mode="json", exclude_unset=True),
        },
        operation=operation,
    )
    return success_response(DeptOut.model_validate(result))


@router.delete("/depts/{dept_id}", summary="删除部门")
async def delete_dept(
    dept_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:dept:delete"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[None]:
    service = DeptService(db, tenant_id)

    async def operation():
        return await service.delete_dept(dept_id, current_user.id, commit=False)

    await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="delete_dept",
        idempotency_key=idempotency_key,
        request_body={"id": dept_id},
        operation=operation,
    )
    return success_response(None)
