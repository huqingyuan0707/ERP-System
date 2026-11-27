"""经营看板聚合（R-01）：销售/采购按业务日期净额、库存/应收应付、趋势与排行。

口径（需求条目拆分.md:672-683）：
  - 今日销售额 = 确认出库合计 - 确认退货合计（按业务日期，草稿不计）；
    今日采购额 = 确认入库合计 - 确认退货合计；
  - 库存总额 = Σ(qty × avg_cost)；应收/应付 = Σ未收/未付（负数表示预收/预付/应退）；
  - 趋势按日分组；Top N 与低库存来自真实表；
  - 所有查询叠加数据范围；缓存 key 含数据范围维度，禁止跨范围复用。
"""

from datetime import date, timedelta
from decimal import Decimal

import json
from typing import Any, Callable, Coroutine

from sqlalchemy import Select, func, select

from app.core.cache import cache_get, cache_set
from app.core.data_scope import ScopeContext, apply_scope, resolve_scope
from app.models.base_data import BdCustomer, BdGoods, BdGoodsCategory, BdSupplier, BdWarehouse
from app.models.finance import FinPayable, FinReceivable
from app.models.inventory import InvAlert, InvStock
from app.models.pur import PurInbound, PurReturn as PurReturnDoc
from app.models.sal import (
    SalOutbound,
    SalOutboundItem,
    SalReturn as SalReturnDoc,
    SalReturnItem,
)
from app.models.sys import SysUser
from app.schemas.dashboard import (
    DashboardOverview,
    InventoryCategoryItem,
    RankItem,
    SalesTrendItem,
    StockAlertItem,
)

ZERO = Decimal("0")
CACHE_TTL = 60
CACHE_PREFIX = "dashboard:"


class DashboardService:
    """只读聚合服务：不写任何业务数据。"""

    def __init__(self, db: Any, tenant_id: int) -> None:
        self._db = db
        self._tenant_id = tenant_id

    async def _scope(self, user: SysUser) -> ScopeContext:
        return await resolve_scope(self._db, user)

    def _cache_key(self, ctx: ScopeContext, name: str, suffix: str = "") -> str:
        """缓存 key 含租户 + 数据范围（本人/本部门/全部）+ 用户与部门，禁止跨范围复用。"""
        return (
            f"{CACHE_PREFIX}{name}:t{self._tenant_id}:s{ctx.scope}"
            f":u{ctx.user_id}:d{ctx.dept_id or 0}:{suffix}"
        )

    async def _cached(self, key: str, loader: Callable[[], Coroutine[Any, Any, str]]) -> str:
        hit = await cache_get(key)
        if hit is not None:
            return hit
        value = await loader()
        await cache_set(key, value, ttl=CACHE_TTL)
        return value

    async def _net_amount(
        self,
        ctx: ScopeContext,
        plus_model: Any,
        plus_date: Any,
        minus_model: Any,
        minus_date: Any,
        start: date | None = None,
        end: date | None = None,
    ) -> Decimal:
        """确认单据净额 = 增加单据合计 - 退货单据合计（均按业务日期）。"""
        plus_stmt = await apply_scope(
            self._db,
            select(func.coalesce(func.sum(plus_model.total_amount), 0)).where(
                plus_model.tenant_id == self._tenant_id,
                plus_model.is_deleted.is_(False),
                plus_model.status == 1,
            ),
            plus_model,
            ctx,
        )
        minus_stmt = await apply_scope(
            self._db,
            select(func.coalesce(func.sum(minus_model.total_amount), 0)).where(
                minus_model.tenant_id == self._tenant_id,
                minus_model.is_deleted.is_(False),
                minus_model.status == 1,
            ),
            minus_model,
            ctx,
        )
        if start is not None:
            plus_stmt = plus_stmt.where(plus_date >= start)
            minus_stmt = minus_stmt.where(minus_date >= start)
        if end is not None:
            plus_stmt = plus_stmt.where(plus_date <= end)
            minus_stmt = minus_stmt.where(minus_date <= end)
        plus_value = (await self._db.execute(plus_stmt)).scalar_one() or ZERO
        minus_value = (await self._db.execute(minus_stmt)).scalar_one() or ZERO
        return Decimal(str(plus_value)) - Decimal(str(minus_value))

    async def _sales_doc_count(self, ctx: ScopeContext, start: date, end: date) -> int:
        """确认销售出库单数（按业务日期，草稿不计）。"""
        stmt = await apply_scope(
            self._db,
            select(func.count(SalOutbound.id)).where(
                SalOutbound.tenant_id == self._tenant_id,
                SalOutbound.is_deleted.is_(False),
                SalOutbound.status == 1,
                SalOutbound.outbound_date >= start,
                SalOutbound.outbound_date <= end,
            ),
            SalOutbound,
            ctx,
        )
        return int((await self._db.execute(stmt)).scalar_one() or 0)

    async def overview(self, user: SysUser) -> DashboardOverview:
        ctx = await self._scope(user)
        key = self._cache_key(ctx, "overview")
        raw = await self._cached(key, lambda: self._load_overview(ctx))
        return DashboardOverview.model_validate_json(raw)

    async def _load_overview(self, ctx: ScopeContext) -> str:
        today = date.today()
        month_start = today.replace(day=1)
        today_sales = await self._net_amount(
            ctx,
            SalOutbound,
            SalOutbound.outbound_date,
            SalReturnDoc,
            SalReturnDoc.return_date,
            start=today,
            end=today,
        )
        month_sales = await self._net_amount(
            ctx,
            SalOutbound,
            SalOutbound.outbound_date,
            SalReturnDoc,
            SalReturnDoc.return_date,
            start=month_start,
            end=today,
        )
        today_purchase = await self._net_amount(
            ctx,
            PurInbound,
            PurInbound.inbound_date,
            PurReturnDoc,
            PurReturnDoc.return_date,
            start=today,
            end=today,
        )
        month_purchase = await self._net_amount(
            ctx,
            PurInbound,
            PurInbound.inbound_date,
            PurReturnDoc,
            PurReturnDoc.return_date,
            start=month_start,
            end=today,
        )
        today_sales_count = await self._sales_doc_count(ctx, today, today)
        month_sales_count = await self._sales_doc_count(ctx, month_start, today)

        inventory_stmt = await apply_scope(
            self._db,
            select(
                func.coalesce(func.sum(InvStock.qty * InvStock.avg_cost), 0),
                func.coalesce(func.sum(InvStock.qty), 0),
            ).where(InvStock.tenant_id == self._tenant_id, InvStock.is_deleted.is_(False)),
            InvStock,
            ctx,
        )
        inventory = (await self._db.execute(inventory_stmt)).one()
        low_stock_stmt = await apply_scope(
            self._db,
            select(func.count(InvStock.id))
            .join(BdGoods, BdGoods.id == InvStock.goods_id)
            .where(
                InvStock.tenant_id == self._tenant_id,
                InvStock.is_deleted.is_(False),
                BdGoods.stock_lower > 0,
                InvStock.available_qty < BdGoods.stock_lower,
            ),
            InvStock,
            ctx,
        )
        low_stock = (await self._db.execute(low_stock_stmt)).scalar_one() or 0

        receivable_stmt = await apply_scope(
            self._db,
            select(func.coalesce(func.sum(FinReceivable.unreceived_amount), 0)).where(
                FinReceivable.tenant_id == self._tenant_id,
                FinReceivable.is_deleted.is_(False),
            ),
            FinReceivable,
            ctx,
        )
        payable_stmt = await apply_scope(
            self._db,
            select(func.coalesce(func.sum(FinPayable.unpaid_amount), 0)).where(
                FinPayable.tenant_id == self._tenant_id,
                FinPayable.is_deleted.is_(False),
            ),
            FinPayable,
            ctx,
        )
        receivable = (await self._db.execute(receivable_stmt)).scalar_one() or ZERO
        payable = (await self._db.execute(payable_stmt)).scalar_one() or ZERO

        alert_stmt = await apply_scope(
            self._db,
            select(func.count(InvAlert.id)).where(
                InvAlert.tenant_id == self._tenant_id,
                InvAlert.is_deleted.is_(False),
                InvAlert.handled.is_(False),
            ),
            InvAlert,
            ctx,
        )
        alerts = (await self._db.execute(alert_stmt)).scalar_one() or 0

        customers_stmt = await apply_scope(
            self._db,
            select(func.count(BdCustomer.id)).where(
                BdCustomer.tenant_id == self._tenant_id, BdCustomer.is_deleted.is_(False)
            ),
            BdCustomer,
            ctx,
        )
        suppliers_stmt = await apply_scope(
            self._db,
            select(func.count(BdSupplier.id)).where(
                BdSupplier.tenant_id == self._tenant_id, BdSupplier.is_deleted.is_(False)
            ),
            BdSupplier,
            ctx,
        )
        customers = (await self._db.execute(customers_stmt)).scalar_one() or 0
        suppliers = (await self._db.execute(suppliers_stmt)).scalar_one() or 0

        return DashboardOverview(
            today_sales_amount=today_sales,
            today_sales_count=today_sales_count,
            month_sales_amount=month_sales,
            month_sales_count=month_sales_count,
            today_purchase_amount=today_purchase,
            month_purchase_amount=month_purchase,
            total_inventory_value=inventory[0],
            total_inventory_qty=inventory[1],
            low_stock_count=int(low_stock),
            unhandled_alert_count=int(alerts),
            total_receivable=receivable,
            total_payable=payable,
            total_customers=int(customers),
            total_suppliers=int(suppliers),
        ).model_dump_json()

    async def trend(self, user: SysUser, days: int, *, sales: bool) -> list[SalesTrendItem]:
        ctx = await self._scope(user)
        suffix = f"{'sales' if sales else 'purchase'}:{days}"
        key = self._cache_key(ctx, "trend", suffix)
        raw = await self._cached(key, lambda: self._load_trend(ctx, days, sales=sales))
        return [SalesTrendItem.model_validate(item) for item in json.loads(raw)]

    async def _load_trend(self, ctx: ScopeContext, days: int, *, sales: bool) -> str:
        end = date.today()
        start = end - timedelta(days=days - 1)
        plus_model = SalOutbound if sales else PurInbound
        plus_date = SalOutbound.outbound_date if sales else PurInbound.inbound_date
        minus_model = SalReturnDoc if sales else PurReturnDoc
        minus_date = SalReturnDoc.return_date if sales else PurReturnDoc.return_date

        plus_stmt = await apply_scope(
            self._db,
            select(
                plus_date,
                func.coalesce(func.sum(plus_model.total_amount), 0),
                func.count(plus_model.id),
            )
            .where(
                plus_model.tenant_id == self._tenant_id,
                plus_model.status == 1,
                plus_model.is_deleted.is_(False),
                plus_date >= start,
                plus_date <= end,
            )
            .group_by(plus_date),
            plus_model,
            ctx,
        )
        minus_stmt = await apply_scope(
            self._db,
            select(minus_date, func.coalesce(func.sum(minus_model.total_amount), 0))
            .where(
                minus_model.tenant_id == self._tenant_id,
                minus_model.status == 1,
                minus_model.is_deleted.is_(False),
                minus_date >= start,
                minus_date <= end,
            )
            .group_by(minus_date),
            minus_model,
            ctx,
        )
        plus_rows = (await self._db.execute(plus_stmt)).all()
        minus_rows = (await self._db.execute(minus_stmt)).all()
        plus_map = {row[0]: (Decimal(str(row[1] or 0)), int(row[2] or 0)) for row in plus_rows}
        minus_map = {row[0]: Decimal(str(row[1] or 0)) for row in minus_rows}
        items = [
            {
                "date": (start + timedelta(days=offset)).isoformat(),
                "amount": str(
                    plus_map.get(start + timedelta(days=offset), (ZERO, 0))[0]
                    - minus_map.get(start + timedelta(days=offset), ZERO)
                ),
                "count": plus_map.get(start + timedelta(days=offset), (ZERO, 0))[1],
            }
            for offset in range(days)
        ]
        return json.dumps(items)

    async def inventory_category(self, user: SysUser) -> list[InventoryCategoryItem]:
        ctx = await self._scope(user)
        key = self._cache_key(ctx, "inventory_category")
        raw = await self._cached(key, lambda: self._load_inventory_category(ctx))
        return [InventoryCategoryItem.model_validate(item) for item in json.loads(raw)]

    async def _load_inventory_category(self, ctx: ScopeContext) -> str:
        stmt = await apply_scope(
            self._db,
            select(
                func.coalesce(BdGoodsCategory.name, "未分类"),
                func.sum(InvStock.qty * InvStock.avg_cost),
                func.sum(InvStock.qty),
            )
            .select_from(InvStock)
            .join(BdGoods, BdGoods.id == InvStock.goods_id)
            .outerjoin(BdGoodsCategory, BdGoodsCategory.id == BdGoods.category_id)
            .where(InvStock.tenant_id == self._tenant_id, InvStock.is_deleted.is_(False))
            .group_by(BdGoodsCategory.name),
            InvStock,
            ctx,
        )
        rows = (await self._db.execute(stmt)).all()
        return json.dumps(
            [
                {
                    "categoryName": str(name),
                    "value": str(value or ZERO),
                    "qty": str(qty or ZERO),
                }
                for name, value, qty in rows
            ]
        )

    async def ranking(self, user: SysUser, kind: str) -> list[RankItem]:
        """客户/商品销售净额 Top 5（按业务日期，确认出库-退货）。"""
        ctx = await self._scope(user)
        key = self._cache_key(ctx, "ranking", kind)
        raw = await self._cached(key, lambda: self._load_ranking(ctx, kind))
        return [RankItem.model_validate(item) for item in json.loads(raw)]

    async def _load_ranking(self, ctx: ScopeContext, kind: str) -> str:
        target = BdCustomer if kind == "customer" else BdGoods
        out_stmt = await apply_scope(
            self._db,
            select(
                target.id,
                target.name,
                func.coalesce(func.sum(SalOutboundItem.amount), 0),
            )
            .join(SalOutbound, SalOutbound.id == SalOutboundItem.outbound_id)
            .join(
                target,
                target.id
                == (SalOutbound.customer_id if kind == "customer" else SalOutboundItem.goods_id),
            )
            .where(
                SalOutbound.tenant_id == self._tenant_id,
                SalOutbound.status == 1,
                SalOutbound.is_deleted.is_(False),
            )
            .group_by(target.id, target.name)
            .order_by(func.sum(SalOutboundItem.amount).desc())
            .limit(5),
            SalOutbound,
            ctx,
        )
        ret_stmt = await apply_scope(
            self._db,
            select(
                target.id,
                func.coalesce(func.sum(SalReturnItem.amount), 0),
            )
            .join(SalReturnDoc, SalReturnDoc.id == SalReturnItem.return_id)
            .join(
                target,
                target.id
                == (SalReturnDoc.customer_id if kind == "customer" else SalReturnItem.goods_id),
            )
            .where(
                SalReturnDoc.tenant_id == self._tenant_id,
                SalReturnDoc.status == 1,
                SalReturnDoc.is_deleted.is_(False),
            )
            .group_by(target.id),
            SalReturnDoc,
            ctx,
        )
        out_rows = (await self._db.execute(out_stmt)).all()
        ret_map = {
            row[0]: Decimal(str(row[1] or 0)) for row in (await self._db.execute(ret_stmt)).all()
        }
        items = [
            {
                "id": int(row[0]),
                "name": str(row[1]),
                "amount": str(Decimal(str(row[2] or 0)) - ret_map.get(int(row[0]), ZERO)),
            }
            for row in out_rows
        ]
        items.sort(key=lambda item: Decimal(str(item["amount"])), reverse=True)
        return json.dumps(items[:5])

    async def low_stocks(self, user: SysUser) -> list[StockAlertItem]:
        ctx = await self._scope(user)
        key = self._cache_key(ctx, "low_stocks")
        raw = await self._cached(key, lambda: self._load_low_stocks(ctx))
        return [StockAlertItem.model_validate(item) for item in json.loads(raw)]

    async def _load_low_stocks(self, ctx: ScopeContext) -> str:
        stmt = await apply_scope(
            self._db,
            select(
                BdWarehouse.name,
                BdGoods.name,
                InvStock.available_qty,
                BdGoods.stock_lower,
            )
            .join(BdGoods, BdGoods.id == InvStock.goods_id)
            .join(BdWarehouse, BdWarehouse.id == InvStock.warehouse_id)
            .where(
                InvStock.tenant_id == self._tenant_id,
                InvStock.is_deleted.is_(False),
                BdGoods.stock_lower > 0,
                InvStock.available_qty < BdGoods.stock_lower,
            )
            .order_by(InvStock.available_qty)
            .limit(10),
            InvStock,
            ctx,
        )
        rows = (await self._db.execute(stmt)).all()
        return json.dumps(
            [
                {
                    "warehouseName": str(warehouse),
                    "goodsName": str(goods),
                    "availableQty": str(available),
                    "stockLower": str(lower),
                }
                for warehouse, goods, available, lower in rows
            ]
        )
