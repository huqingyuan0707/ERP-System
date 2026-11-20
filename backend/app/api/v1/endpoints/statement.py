"""供应商对账单端点（F-06）：列表/详情/预览/生成快照，导出走 C-07。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.statement import (
    StatementBuildOut,
    StatementCreate,
    StatementDetailOut,
    StatementOut,
    StatementQuery,
)
from app.services.statement import StatementService

router = APIRouter(prefix="/finance/statements", tags=["财务-对账单"])

StatementReader = Annotated[SysUser, Depends(require_perm("fin:statement:list"))]
StatementCreator = Annotated[SysUser, Depends(require_perm("fin:statement:add"))]


@router.get("", summary="对账单列表")
async def list_statements(
    query: Annotated[StatementQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: StatementReader,
) -> ApiResponse[PageResult[StatementOut]]:
    """按往来单位/期间筛选；数据范围由后端叠加。"""
    result = await StatementService(db, tenant_id).list(query, current_user)
    return success_response(result)


@router.post("/preview", summary="生成前预览")
async def preview_statement(
    payload: StatementCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: StatementCreator,
) -> ApiResponse[StatementBuildOut]:
    """按业务日期重算期初/本期/期末，不落库；生成前先看数。"""
    return success_response(await StatementService(db, tenant_id).build(current_user, payload))


@router.post("", summary="生成对账单快照")
async def create_statement(
    payload: StatementCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: StatementCreator,
) -> ApiResponse[StatementDetailOut]:
    """生成快照；同供应商+期间已存在返回 409，快照不可改。"""
    return success_response(await StatementService(db, tenant_id).create(current_user, payload))


@router.get("/{statement_id}", summary="对账单详情")
async def get_statement(
    statement_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: StatementReader,
) -> ApiResponse[StatementDetailOut]:
    """只读快照明细。"""
    return success_response(await StatementService(db, tenant_id).get(statement_id, current_user))
