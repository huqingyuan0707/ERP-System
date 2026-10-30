"""字典端点：登录用户分组查询；维护接口按 sys:dict:* 权限。"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUserDep, DbDep, require_perm
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


router = APIRouter(prefix="/sys/dicts", tags=["数据字典"])


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
    _: Annotated[SysUser, Depends(require_perm("base:dict:add"))],
) -> ApiResponse[DictTypeOut]:
    result = await DictService(db).create_type(payload)
    return success_response(DictTypeOut.model_validate(result))


@router.put("/types/{type_id}", summary="修改字典类型")
async def update_type(
    type_id: int,
    payload: DictTypeUpdate,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:dict:edit"))],
) -> ApiResponse[DictTypeOut]:
    result = await DictService(db).update_type(type_id, payload)
    return success_response(DictTypeOut.model_validate(result))


@router.delete("/types/{type_id}", summary="删除字典类型")
async def delete_type(
    type_id: int,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:dict:delete"))],
) -> ApiResponse[None]:
    await DictService(db).delete_type(type_id)
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
    _: Annotated[SysUser, Depends(require_perm("base:dict:add"))],
) -> ApiResponse[DictItemOut]:
    result = await DictService(db).create_item(type_code, payload)
    return success_response(DictItemOut.model_validate(result))


@router.put("/items/{item_id}", summary="修改字典项")
async def update_item(
    item_id: int,
    payload: DictItemUpdate,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:dict:edit"))],
) -> ApiResponse[DictItemOut]:
    result = await DictService(db).update_item(item_id, payload)
    return success_response(DictItemOut.model_validate(result))


@router.delete("/items/{item_id}", summary="删除字典项")
async def delete_item(
    item_id: int,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:dict:delete"))],
) -> ApiResponse[None]:
    await DictService(db).delete_item(item_id)
    return success_response(None)
