"""系统参数端点：登录用户读取；维护按 sys:config:* 权限。

批量保存经 IdempotencyService 包装：带 Idempotency-Key 时业务与幂等记录
同事务提交、重放返回首次结果；不带键时行为与改造前一致。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header

from app.core.deps import CurrentUserDep, DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, success_response
from app.schemas.config import ConfigBatchUpdate, ConfigOut
from app.services.config import ConfigService
from app.services.idempotency import IdempotencyService


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
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sys:config:edit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[list[ConfigOut]]:
    """批量保存；operation 不自行提交，由幂等服务统一提交业务与记录。"""
    service = ConfigService(db, auto_commit=False)

    async def operation():
        entities = await service.batch_update(
            payload.items,
            operator_id=current_user.id,
            tenant_id=tenant_id,
        )
        # 逐条转成出参并按 camelCase 别名输出，交给统一响应包装
        return [
            ConfigOut.model_validate(c).model_dump(mode="json", by_alias=True)
            for c in entities
        ]

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="update_sys_config",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response([ConfigOut.model_validate(c) for c in result])
