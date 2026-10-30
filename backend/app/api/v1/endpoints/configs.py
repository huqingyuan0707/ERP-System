"""系统参数端点：登录用户读取；维护按 sys:config:* 权限。"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUserDep, DbDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, success_response
from app.schemas.config import ConfigBatchUpdate, ConfigOut
from app.services.config import ConfigService


router = APIRouter(prefix="/sys/configs", tags=["系统参数"])


@router.get("", summary="参数列表（登录用户）")
async def list_configs(
    db: DbDep,
    _: CurrentUserDep,
) -> ApiResponse[list[ConfigOut]]:
    result = await ConfigService(db).list_all()
    return success_response([ConfigOut.model_validate(c) for c in result])


@router.put("", summary="批量保存参数")
async def update_configs(
    payload: ConfigBatchUpdate,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("sys:config:edit"))],
) -> ApiResponse[list[ConfigOut]]:
    result = await ConfigService(db).batch_update(payload.items)
    return success_response([ConfigOut.model_validate(c) for c in result])
