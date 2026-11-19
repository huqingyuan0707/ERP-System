"""采购报表服务（R-02）：已确认入库为正行、已确认退货为负行，统一筛选与数据范围。

口径（需求条目拆分.md:685-696）：
  - 草稿不计；入库按 inbound_date、退货按 return_date 归集；
  - 退货只在报表以负数展示，财务账仍由 P-04 直接冲减原应付，不生成红字；
  - detail/summary/rank 三种视图共用同一批行，合计 = 入库 - 退货。
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import apply_scope, resolve_scope
from app.models.base_data import BdGoods, BdSupplier, BdWarehouse
from app.models.pur import PurInbound, PurInboundItem, PurReturn, PurReturnItem
from app.models.sys import SysUser
from app.schemas.purchase_report import (
    ZERO,
    PurchaseReportOut,
    PurchaseReportQuery,
    PurchaseReportTotals,
)


class PurchaseReportService:
    """只读取已确认单据，不写任何业务数据。"""

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self._db = db
        self._tenant_id = tenant_id

    @staticmethod
    def _match_date(query: PurchaseReportQuery, day: Any) -> bool:
        if query.start_date is not None and day < query.start_date:
            return False
        if query.end_date is not None and day > query.end_date:
            return False
        return True

    async def build_rows(self, user: SysUser, query: PurchaseReportQuery) -> list[dict[str, Any]]:
        """取两种单据的已确认明细，合并为统一行（退货数量/金额为负）。"""
        ctx = await resolve_scope(self._db, user)

        inbound_stmt = (
            select(
                PurInbound,
                PurInboundItem,
                BdSupplier.name,
                BdGoods.code,
                BdGoods.name,
                BdWarehouse.name,
            )
            .join(PurInboundItem, PurInboundItem.inbound_id == PurInbound.id)
            .join(BdSupplier, BdSupplier.id == PurInbound.supplier_id)
            .join(BdGoods, BdGoods.id == PurInboundItem.goods_id)
            .outerjoin(BdWarehouse, BdWarehouse.id == PurInbound.warehouse_id)
            .where(
                PurInbound.tenant_id == self._tenant_id,
                PurInbound.is_deleted.is_(False),
                PurInbound.status == 1,
                PurInboundItem.is_deleted.is_(False),
            )
        )
        inbound_stmt = await apply_scope(self._db, inbound_stmt, PurInbound, ctx)
        if query.supplier_id is not None:
            inbound_stmt = inbound_stmt.where(PurInbound.supplier_id == query.supplier_id)
        if query.goods_id is not None:
            inbound_stmt = inbound_stmt.where(PurInboundItem.goods_id == query.goods_id)
        if query.warehouse_id is not None:
            inbound_stmt = inbound_stmt.where(PurInbound.warehouse_id == query.warehouse_id)

        return_stmt = (
            select(
                PurReturn,
                PurReturnItem,
                BdSupplier.name,
                BdGoods.code,
                BdGoods.name,
                BdWarehouse.name,
            )
            .join(PurReturnItem, PurReturnItem.return_id == PurReturn.id)
            .join(BdSupplier, BdSupplier.id == PurReturn.supplier_id)
            .join(BdGoods, BdGoods.id == PurReturnItem.goods_id)
            .outerjoin(BdWarehouse, BdWarehouse.id == PurReturn.warehouse_id)
            .where(
                PurReturn.tenant_id == self._tenant_id,
                PurReturn.is_deleted.is_(False),
                PurReturn.status == 1,
                PurReturnItem.is_deleted.is_(False),
            )
        )
        return_stmt = await apply_scope(self._db, return_stmt, PurReturn, ctx)
        if query.supplier_id is not None:
            return_stmt = return_stmt.where(PurReturn.supplier_id == query.supplier_id)
        if query.goods_id is not None:
            return_stmt = return_stmt.where(PurReturnItem.goods_id == query.goods_id)
        if query.warehouse_id is not None:
            return_stmt = return_stmt.where(PurReturn.warehouse_id == query.warehouse_id)

        rows: list[dict[str, Any]] = []
        for order, item, supplier_name, goods_code, goods_name, warehouse_name in (
            await self._db.execute(inbound_stmt)
        ).all():
            if not self._match_date(query, order.inbound_date):
                continue
            rows.append(
                {
                    "type": "inbound",
                    "sourceNo": order.inbound_no,
                    "docNo": order.inbound_no,
                    "date": order.inbound_date,
                    "supplierId": order.supplier_id,
                    "supplierName": supplier_name,
                    "goodsId": item.goods_id,
                    "goodsCode": goods_code,
                    "goodsName": goods_name,
                    "warehouseId": order.warehouse_id,
                    "warehouseName": warehouse_name,
                    "qty": item.qty,
                    "price": item.price,
                    "amount": item.amount,
                }
            )
        for ret, item, supplier_name, goods_code, goods_name, warehouse_name in (
            await self._db.execute(return_stmt)
        ).all():
            if not self._match_date(query, ret.return_date):
                continue
            rows.append(
                {
                    "type": "return",
                    "sourceNo": ret.source_no,
                    "docNo": ret.return_no,
                    "date": ret.return_date,
                    "supplierId": ret.supplier_id,
                    "supplierName": supplier_name,
                    "goodsId": item.goods_id,
                    "goodsCode": goods_code,
                    "goodsName": goods_name,
                    "warehouseId": ret.warehouse_id,
                    "warehouseName": warehouse_name,
                    "qty": -item.qty,
                    "price": item.price,
                    "amount": -item.amount,
                }
            )
        rows.sort(key=lambda row: (str(row["date"]), str(row["docNo"])), reverse=True)
        return rows

    @staticmethod
    def _totals(rows: list[dict[str, Any]]) -> PurchaseReportTotals:
        inbound_qty = sum((r["qty"] for r in rows if r["type"] == "inbound"), ZERO)
        return_qty = sum((-r["qty"] for r in rows if r["type"] == "return"), ZERO)
        inbound_amount = sum((r["amount"] for r in rows if r["type"] == "inbound"), ZERO)
        return_amount = sum((-r["amount"] for r in rows if r["type"] == "return"), ZERO)
        return PurchaseReportTotals(
            inbound_qty=inbound_qty,
            return_qty=return_qty,
            net_qty=inbound_qty - return_qty,
            inbound_amount=inbound_amount,
            return_amount=return_amount,
            net_amount=inbound_amount - return_amount,
            row_count=len(rows),
        )

    @staticmethod
    def _summary(rows: list[dict[str, Any]], group_by: str) -> list[dict[str, Any]]:
        id_key = "goodsId" if group_by == "goods" else "supplierId"
        name_key = "goodsName" if group_by == "goods" else "supplierName"
        grouped: dict[int, dict[str, Any]] = {}
        for row in rows:
            group = grouped.setdefault(
                int(row[id_key]),
                {
                    "groupId": int(row[id_key]),
                    "groupName": row[name_key],
                    "inboundQty": ZERO,
                    "returnQty": ZERO,
                    "inboundAmount": ZERO,
                    "returnAmount": ZERO,
                },
            )
            if row["type"] == "inbound":
                group["inboundQty"] += row["qty"]
                group["inboundAmount"] += row["amount"]
            else:
                group["returnQty"] += -row["qty"]
                group["returnAmount"] += -row["amount"]
        items = []
        for group in grouped.values():
            group["netQty"] = group["inboundQty"] - group["returnQty"]
            group["netAmount"] = group["inboundAmount"] - group["returnAmount"]
            items.append(group)
        items.sort(key=lambda item: item["netAmount"], reverse=True)
        return items

    @staticmethod
    def _rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[int, dict[str, Any]] = {}
        for row in rows:
            group = grouped.setdefault(
                int(row["supplierId"]),
                {
                    "supplierId": int(row["supplierId"]),
                    "supplierName": row["supplierName"],
                    "netAmount": ZERO,
                    "netQty": ZERO,
                },
            )
            group["netAmount"] += row["amount"]
            group["netQty"] += row["qty"]
        items = sorted(grouped.values(), key=lambda item: item["netAmount"], reverse=True)[:10]
        for index, item in enumerate(items, start=1):
            item["rank"] = index
        return items

    async def get_report(self, user: SysUser, query: PurchaseReportQuery) -> PurchaseReportOut:
        """按视图返回分页行与合计；合计始终基于全量筛选结果，不受分页影响。"""
        rows = await self.build_rows(user, query)
        if query.view == "summary":
            items = self._summary(rows, query.group_by)
        elif query.view == "rank":
            items = self._rank(rows)
        else:
            items = rows
        total = len(items)
        start = (query.page - 1) * query.page_size
        return PurchaseReportOut(
            view=query.view,
            total=total,
            page=query.page,
            page_size=query.page_size,
            items=items[start : start + query.page_size],
            totals=self._totals(rows),
        )
