"""销售报表服务（R-03）：已确认出库为正行、已确认退货为负行，统一筛选与数据范围。

口径（需求条目拆分.md:698-709）：
  - 草稿不计；出库按 outbound_date、退货按 return_date 归集；
  - 退货只在报表以负数展示，财务账仍由 SA-04 直接冲减原应收，不生成红字；
  - detail/summary/customer_rank/goods_rank 四视图共用同一批行，合计 = 出库 - 退货；
  - 合计基于全量筛选结果，不受分页影响。
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import apply_scope, resolve_scope
from app.models.base_data import BdCustomer, BdGoods, BdWarehouse
from app.models.sal import SalOutbound, SalOutboundItem, SalReturn, SalReturnItem
from app.models.sys import SysUser
from app.schemas.sales_report import (
    ZERO,
    SalesReportOut,
    SalesReportQuery,
    SalesReportTotals,
)


class SalesReportService:
    """只读取已确认单据，不写任何业务数据。"""

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self._db = db
        self._tenant_id = tenant_id

    @staticmethod
    def _match_date(query: SalesReportQuery, day: Any) -> bool:
        if query.start_date is not None and day < query.start_date:
            return False
        if query.end_date is not None and day > query.end_date:
            return False
        return True

    async def build_rows(self, user: SysUser, query: SalesReportQuery) -> list[dict[str, Any]]:
        ctx = await resolve_scope(self._db, user)

        outbound_stmt = (
            select(
                SalOutbound,
                SalOutboundItem,
                BdCustomer.name,
                BdGoods.code,
                BdGoods.name,
                BdWarehouse.name,
            )
            .join(SalOutboundItem, SalOutboundItem.outbound_id == SalOutbound.id)
            .join(BdCustomer, BdCustomer.id == SalOutbound.customer_id)
            .join(BdGoods, BdGoods.id == SalOutboundItem.goods_id)
            .outerjoin(BdWarehouse, BdWarehouse.id == SalOutbound.warehouse_id)
            .where(
                SalOutbound.tenant_id == self._tenant_id,
                SalOutbound.is_deleted.is_(False),
                SalOutbound.status == 1,
                SalOutboundItem.is_deleted.is_(False),
            )
        )
        outbound_stmt = await apply_scope(self._db, outbound_stmt, SalOutbound, ctx)
        if query.customer_id is not None:
            outbound_stmt = outbound_stmt.where(SalOutbound.customer_id == query.customer_id)
        if query.goods_id is not None:
            outbound_stmt = outbound_stmt.where(SalOutboundItem.goods_id == query.goods_id)
        if query.warehouse_id is not None:
            outbound_stmt = outbound_stmt.where(SalOutbound.warehouse_id == query.warehouse_id)

        return_stmt = (
            select(
                SalReturn,
                SalReturnItem,
                BdCustomer.name,
                BdGoods.code,
                BdGoods.name,
                BdWarehouse.name,
            )
            .join(SalReturnItem, SalReturnItem.return_id == SalReturn.id)
            .join(BdCustomer, BdCustomer.id == SalReturn.customer_id)
            .join(BdGoods, BdGoods.id == SalReturnItem.goods_id)
            .outerjoin(BdWarehouse, BdWarehouse.id == SalReturn.warehouse_id)
            .where(
                SalReturn.tenant_id == self._tenant_id,
                SalReturn.is_deleted.is_(False),
                SalReturn.status == 1,
                SalReturnItem.is_deleted.is_(False),
            )
        )
        return_stmt = await apply_scope(self._db, return_stmt, SalReturn, ctx)
        if query.customer_id is not None:
            return_stmt = return_stmt.where(SalReturn.customer_id == query.customer_id)
        if query.goods_id is not None:
            return_stmt = return_stmt.where(SalReturnItem.goods_id == query.goods_id)
        if query.warehouse_id is not None:
            return_stmt = return_stmt.where(SalReturn.warehouse_id == query.warehouse_id)

        rows: list[dict[str, Any]] = []
        for outbound, item, customer_name, goods_code, goods_name, warehouse_name in (
            await self._db.execute(outbound_stmt)
        ).all():
            if not self._match_date(query, outbound.outbound_date):
                continue
            rows.append(
                {
                    "type": "outbound",
                    "sourceNo": outbound.outbound_no,
                    "docNo": outbound.outbound_no,
                    "date": outbound.outbound_date,
                    "customerId": outbound.customer_id,
                    "customerName": customer_name,
                    "goodsId": item.goods_id,
                    "goodsCode": goods_code,
                    "goodsName": goods_name,
                    "warehouseId": outbound.warehouse_id,
                    "warehouseName": warehouse_name,
                    "qty": item.qty,
                    "price": item.price,
                    "amount": item.amount,
                }
            )
        for ret, item, customer_name, goods_code, goods_name, warehouse_name in (
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
                    "customerId": ret.customer_id,
                    "customerName": customer_name,
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
    def _totals(rows: list[dict[str, Any]]) -> SalesReportTotals:
        outbound_qty = sum((r["qty"] for r in rows if r["type"] == "outbound"), ZERO)
        return_qty = sum((-r["qty"] for r in rows if r["type"] == "return"), ZERO)
        outbound_amount = sum((r["amount"] for r in rows if r["type"] == "outbound"), ZERO)
        return_amount = sum((-r["amount"] for r in rows if r["type"] == "return"), ZERO)
        return SalesReportTotals(
            outbound_qty=outbound_qty,
            return_qty=return_qty,
            net_qty=outbound_qty - return_qty,
            outbound_amount=outbound_amount,
            return_amount=return_amount,
            net_amount=outbound_amount - return_amount,
            row_count=len(rows),
        )

    @staticmethod
    def _summary(rows: list[dict[str, Any]], group_by: str) -> list[dict[str, Any]]:
        id_key = "goodsId" if group_by == "goods" else "customerId"
        name_key = "goodsName" if group_by == "goods" else "customerName"
        grouped: dict[int, dict[str, Any]] = {}
        for row in rows:
            group = grouped.setdefault(
                int(row[id_key]),
                {
                    "groupId": int(row[id_key]),
                    "groupName": row[name_key],
                    "outboundQty": ZERO,
                    "returnQty": ZERO,
                    "outboundAmount": ZERO,
                    "returnAmount": ZERO,
                },
            )
            if row["type"] == "outbound":
                group["outboundQty"] += row["qty"]
                group["outboundAmount"] += row["amount"]
            else:
                group["returnQty"] += -row["qty"]
                group["returnAmount"] += -row["amount"]
        items = []
        for group in grouped.values():
            group["netQty"] = group["outboundQty"] - group["returnQty"]
            group["netAmount"] = group["outboundAmount"] - group["returnAmount"]
            items.append(group)
        items.sort(key=lambda item: item["netAmount"], reverse=True)
        return items

    @staticmethod
    def _rank(rows: list[dict[str, Any]], by_customer: bool) -> list[dict[str, Any]]:
        id_key = "customerId" if by_customer else "goodsId"
        name_key = "customerName" if by_customer else "goodsName"
        grouped: dict[int, dict[str, Any]] = {}
        for row in rows:
            group = grouped.setdefault(
                int(row[id_key]),
                {
                    "rankId": int(row[id_key]),
                    "rankName": row[name_key],
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

    async def get_report(self, user: SysUser, query: SalesReportQuery) -> SalesReportOut:
        rows = await self.build_rows(user, query)
        if query.view == "summary":
            items = self._summary(rows, query.group_by)
        elif query.view == "customer_rank":
            items = self._rank(rows, by_customer=True)
        elif query.view == "goods_rank":
            items = self._rank(rows, by_customer=False)
        else:
            items = rows
        total = len(items)
        start = (query.page - 1) * query.page_size
        return SalesReportOut(
            view=query.view,
            total=total,
            page=query.page,
            page_size=query.page_size,
            items=items[start : start + query.page_size],
            totals=self._totals(rows),
        )
