"""经营看板只读聚合（R-01 第一步）。

销售订单/出库（SA-03/SA-04）尚未实现，因此：
  - 概览里的销售额/单数固定为 0，不返回模拟数据；
  - 趋势图返回「已确认采购入库 − 已确认采购退货」的按日净额（真实数据）；
  - 库存、低库存、应收应付、客户供应商、库存分类均为真实表聚合。

所有查询按 tenant_id 过滤，看板只读、不写业务数据。
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import Select, func, select

from app.core.deps import CurrentUserDep, DbDep, TenantDep
from app.models.base_data import BdCustomer, BdGoods, BdGoodsCategory, BdSupplier
from app.models.finance import FinPayable, FinReceivable
from app.models.inventory import InvStock
from app.models.pur import PurInbound, PurReturn
from app.schemas.common import ApiResponse, success_response
from app.schemas.dashboard import DashboardOverview, InventoryCategoryItem, SalesTrendItem

router = APIRouter(prefix="/dashboard", tags=["经营看板"])

ZERO = Decimal("0")


async def _scalar(db: Any, stmt: Select, default: Any = ZERO) -> Any:
    """聚合查询统一取值，空结果回落到默认值。"""
    value = (await db.execute(stmt)).scalar_one()
    return default if value is None else value


@router.get("/overview", summary="经营看板概览")
async def get_overview(
    db: DbDep,
    tenant_id: TenantDep,
    _: CurrentUserDep,
) -> ApiResponse[DashboardOverview]:
    """概览：库存与财务指标来自真实表；销售类指标待 SA-03/SA-04 上线后接入。"""
    inventory = (
        await db.execute(
            select(
                func.coalesce(func.sum(InvStock.qty * InvStock.avg_cost), 0),
                func.coalesce(func.sum(InvStock.qty), 0),
            ).where(
                InvStock.tenant_id == tenant_id,
                InvStock.is_deleted.is_(False),
            )
        )
    ).one()

    # 低库存口径：商品设置了下限（stock_lower > 0）且可用量低于下限，
    # 不再使用旧实现里写死的「available_qty < 10」。
    low_stock = await _scalar(
        db,
        select(func.count(InvStock.id))
        .join(BdGoods, BdGoods.id == InvStock.goods_id)
        .where(
            InvStock.tenant_id == tenant_id,
            InvStock.is_deleted.is_(False),
            BdGoods.stock_lower > 0,
            InvStock.available_qty < BdGoods.stock_lower,
        ),
        default=0,
    )
    payable = await _scalar(
        db,
        select(func.coalesce(func.sum(FinPayable.unpaid_amount), 0)).where(
            FinPayable.tenant_id == tenant_id,
            FinPayable.is_deleted.is_(False),
        ),
    )
    receivable = await _scalar(
        db,
        select(func.coalesce(func.sum(FinReceivable.unreceived_amount), 0)).where(
            FinReceivable.tenant_id == tenant_id,
            FinReceivable.is_deleted.is_(False),
        ),
    )
    customers = await _scalar(
        db,
        select(func.count(BdCustomer.id)).where(
            BdCustomer.tenant_id == tenant_id,
            BdCustomer.is_deleted.is_(False),
        ),
        default=0,
    )
    suppliers = await _scalar(
        db,
        select(func.count(BdSupplier.id)).where(
            BdSupplier.tenant_id == tenant_id,
            BdSupplier.is_deleted.is_(False),
        ),
        default=0,
    )

    return success_response(
        DashboardOverview(
            today_sales_amount=ZERO,
            today_sales_count=0,
            month_sales_amount=ZERO,
            month_sales_count=0,
            total_inventory_value=inventory[0],
            total_inventory_qty=inventory[1],
            low_stock_count=int(low_stock),
            total_receivable=receivable,
            total_payable=payable,
            total_customers=int(customers),
            total_suppliers=int(suppliers),
        )
    )


@router.get("/sales-trend", summary="近 N 天采购净额趋势")
async def get_sales_trend(
    db: DbDep,
    tenant_id: TenantDep,
    _: CurrentUserDep,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> ApiResponse[list[SalesTrendItem]]:
    """销售出库未上线，先以「已确认入库 − 已确认退货」的真实净额填充趋势。

    按业务日期（inbound_date / return_date）分组，只统计已确认单据；
    净额允许为负（当天只有退货），与文档 R-01 的冲减口径一致。
    """
    end = date.today()
    start = end - timedelta(days=days - 1)

    inbound = (
        await db.execute(
            select(
                PurInbound.inbound_date,
                func.sum(PurInbound.total_amount),
                func.count(PurInbound.id),
            )
            .where(
                PurInbound.tenant_id == tenant_id,
                PurInbound.status == 1,
                PurInbound.is_deleted.is_(False),
                PurInbound.inbound_date >= start,
                PurInbound.inbound_date <= end,
            )
            .group_by(PurInbound.inbound_date)
        )
    ).all()
    returns = (
        await db.execute(
            select(
                PurReturn.return_date,
                func.sum(PurReturn.total_amount),
                func.count(PurReturn.id),
            )
            .where(
                PurReturn.tenant_id == tenant_id,
                PurReturn.status == 1,
                PurReturn.is_deleted.is_(False),
                PurReturn.return_date >= start,
                PurReturn.return_date <= end,
            )
            .group_by(PurReturn.return_date)
        )
    ).all()

    in_map = {row[0]: (row[1] or ZERO, int(row[2] or 0)) for row in inbound}
    ret_map = {row[0]: row[1] or ZERO for row in returns}
    items = [
        SalesTrendItem(
            date=start + timedelta(days=offset),
            amount=in_map.get(start + timedelta(days=offset), (ZERO, 0))[0]
            - ret_map.get(start + timedelta(days=offset), ZERO),
            count=in_map.get(start + timedelta(days=offset), (ZERO, 0))[1],
        )
        for offset in range(days)
    ]
    return success_response(items)


@router.get("/inventory-category", summary="按商品分类统计库存")
async def get_inventory_category(
    db: DbDep,
    tenant_id: TenantDep,
    _: CurrentUserDep,
) -> ApiResponse[list[InventoryCategoryItem]]:
    """按商品分类聚合 Σ(qty×avg_cost) 与 Σ(qty)，无分类的归入「未分类」。"""
    rows = (
        await db.execute(
            select(
                func.coalesce(BdGoodsCategory.name, "未分类"),
                func.sum(InvStock.qty * InvStock.avg_cost),
                func.sum(InvStock.qty),
            )
            .select_from(InvStock)
            .join(BdGoods, BdGoods.id == InvStock.goods_id)
            .outerjoin(BdGoodsCategory, BdGoodsCategory.id == BdGoods.category_id)
            .where(
                InvStock.tenant_id == tenant_id,
                InvStock.is_deleted.is_(False),
            )
            .group_by(BdGoodsCategory.name)
        )
    ).all()
    return success_response(
        [
            InventoryCategoryItem(
                category_name=str(name),
                value=value or ZERO,
                qty=qty or ZERO,
            )
            for name, value, qty in rows
        ]
    )
