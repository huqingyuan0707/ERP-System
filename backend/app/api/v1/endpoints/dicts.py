"""字典端点：登录用户分组查询；维护接口按 sys:dict:* 权限。

写操作统一经 IdempotencyService 包装：带 Idempotency-Key 时业务与幂等记录
同事务提交、重放返回首次结果；不带键时行为与改造前一致。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header

from app.core.deps import CurrentUserDep, DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, success_response
from app.schemas.dict import (
    DictItemCreate,
    DictItemOut,
    DictItemUpdate,
    DictTypeCreate,
    DictTypeOut,
    DictTypeUpdate,
)
from app.services.dict import DictService
from app.services.idempotency import IdempotencyService


router = APIRouter(prefix="/sys/dicts", tags=["数据字典"])

IdempotencyKeyHeader = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
]


@router.get("", summary="全量字典（按类型分组，登录用户）")
async def list_grouped(
    db: DbDep,
    _: CurrentUserDep,
) -> ApiResponse[dict[str, list[dict[str, str]]]]:
    """返回 {typeCode: [{label, value}]}，前端 dictStore 一次拉取并缓存。"""
    result = await DictService(db).get_grouped()
    return success_response(result)


@router.get("/types", summary="字典类型列表（维护）")
async def list_types(
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:dict:list"))],
) -> ApiResponse[list[DictTypeOut]]:
    result = await DictService(db).list_types()
    return success_response([DictTypeOut.model_validate(t) for t in result])


@router.post("/types", summary="新增字典类型")
async def create_type(
    payload: DictTypeCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:dict:add"))],
    idempotency_key: IdempotencyKeyHeader = None,
) -> ApiResponse[DictTypeOut]:
    service = DictService(db, auto_commit=False)

    async def operation():
        return DictTypeOut.model_validate(await service.create_type(payload))

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="create_dict_type",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(DictTypeOut.model_validate(result))


@router.put("/types/{type_id}", summary="修改字典类型")
async def update_type(
    type_id: int,
    payload: DictTypeUpdate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:dict:edit"))],
    idempotency_key: IdempotencyKeyHeader = None,
) -> ApiResponse[DictTypeOut]:
    service = DictService(db, auto_commit=False)

    async def operation():
        return DictTypeOut.model_validate(await service.update_type(type_id, payload))

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="update_dict_type",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(DictTypeOut.model_validate(result))


@router.delete("/types/{type_id}", summary="删除字典类型")
async def delete_type(
    type_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:dict:delete"))],
    idempotency_key: IdempotencyKeyHeader = None,
) -> ApiResponse[None]:
    service = DictService(db, auto_commit=False)

    async def operation():
        await service.delete_type(type_id)

    await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="delete_dict_type",
        idempotency_key=idempotency_key,
        request_body={"type_id": type_id},
        operation=operation,
    )
    return success_response(None)


@router.get("/types/{type_code}/items", summary="字典项列表（维护）")
async def list_items(
    type_code: str,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:dict:list"))],
) -> ApiResponse[list[DictItemOut]]:
    result = await DictService(db).list_items(type_code)
    return success_response([DictItemOut.model_validate(i) for i in result])


@router.post("/types/{type_code}/items", summary="新增字典项")
async def create_item(
    type_code: str,
    payload: DictItemCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:dict:add"))],
    idempotency_key: IdempotencyKeyHeader = None,
) -> ApiResponse[DictItemOut]:
    service = DictService(db, auto_commit=False)

    async def operation():
        return DictItemOut.model_validate(await service.create_item(type_code, payload))

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="create_dict_item",
        idempotency_key=idempotency_key,
        request_body={"type_code": type_code, **payload.model_dump(mode="json")},
        operation=operation,
    )
    return success_response(DictItemOut.model_validate(result))


@router.put("/items/{item_id}", summary="修改字典项")
async def update_item(
    item_id: int,
    payload: DictItemUpdate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:dict:edit"))],
    idempotency_key: IdempotencyKeyHeader = None,
) -> ApiResponse[DictItemOut]:
    service = DictService(db, auto_commit=False)

    async def operation():
        return DictItemOut.model_validate(await service.update_item(item_id, payload))

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="update_dict_item",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(DictItemOut.model_validate(result))


@router.delete("/items/{item_id}", summary="删除字典项")
async def delete_item(
    item_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:dict:delete"))],
    idempotency_key: IdempotencyKeyHeader = None,
) -> ApiResponse[None]:
    service = DictService(db, auto_commit=False)

    async def operation():
        await service.delete_item(item_id)

    await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="delete_dict_item",
        idempotency_key=idempotency_key,
        request_body={"item_id": item_id},
        operation=operation,
    )
    return success_response(None)
