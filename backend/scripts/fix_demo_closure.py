"""演示数据闭环修复脚本。

修复目标（一次性执行，幂等：已修复的部分自动跳过）：
  1. 采购闭环：订单数量对齐到库存流水 -> 生成采购入库单（已确认）-> 回写
     pur_order_item.inbound_qty -> 订单状态 4完成/3部分入库 -> 流水改指向入库单；
  2. 销售闭环：按 sal_out 流水生成销售订单（已审/完成）+ 销售出库单（已确认）
     + 明细 + 回写 outbound_qty -> 流水改指向出库单；
  3. 盘点闭环：为 check_gain 流水补真实的盘点单 IC202610120001；
  4. 期初建账：为 6 条无流水库存 + 首笔流水 before_qty>0 的链条补 2026-09-01
     期初盘点单与 check_gain 流水，使每条库存链条都能从 0 追溯；
  5. 左1仓库建账：期初盘点单 + 库存 + 流水，避免多仓库页面空白。

约定：amount = qty*price（价税合计），tax_amount = Σ amount*tax_rate/(1+tax_rate)；
pur_inbound/sal_outbound/inv_check.status = 1 已确认；pur_order 完成后 4 / 部分 3；
sal_order 全部出库后 4；单据编号 = 前缀 + YYYYMMDD(流水日期) + 4位序号。
"""
import asyncio
import datetime as dt
import os
from decimal import ROUND_HALF_UP, Decimal

import asyncpg


# 数据库连接串从环境变量读取，禁止把账号密码写进仓库
# （后端 .env 的 DATABASE_URL 需换成 asyncpg 驱动串 postgresql+asyncpg://）
DSN = os.environ["ERP_DEMO_DSN"]
ADMIN = 1
MAIN_WH = 1
LEFT_WH = 2
TAX_RATE = Decimal("0.13")
D0 = Decimal("0")

PO_RENAME = {  # 草稿订单 -> 按流水日期重编单号（order_date 同步改为流水日）
    "PO202611030007": ("PO202610010001", "2026-10-01"),
    "PO202611030008": ("PO202610070001", "2026-10-07"),
    "PO202611030009": ("PO202610130001", "2026-10-13"),
    "PO202611030010": ("PO202610090001", "2026-10-09"),
}
# 有流水但需要补建销售单据的出库流水（source_no -> 新订单号/出库单号）
SALES = [
    ("SO202611050001", "SO202610200001", "SI202610200001"),
    ("SO202611050002", "SO202610280001", "SI202610280001"),
    ("SO202611050003", "SO202610300001", "SI202610300001"),
    ("SO202611050004", "SO202610150001", "SI202610150001"),
    ("SO202611050005", "SO202610180001", "SI202610180001"),
    ("SO202611050006", "SO202610270001", "SI202610270001"),
    ("SO202611050007", "SO202610310001", "SI202610310001"),
]
OPENING_DATE = "2026-09-01"
LEFT_GOODS_N = 30


def tax_of(amounts_rates: list[tuple[Decimal, Decimal]]) -> Decimal:
    total = sum((a * r / (1 + r) for a, r in amounts_rates), D0)
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def fix_purchase(conn: asyncpg.Connection) -> None:
    if await conn.fetchval("SELECT COUNT(*) FROM pur_inbound"):
        print("采购闭环：已存在入库单，跳过")
        return
    flows = await conn.fetch(
        "SELECT source_no, goods_id, qty, biz_time FROM inv_stock_flow "
        "WHERE source_type='pur_order' ORDER BY source_no, goods_id"
    )
    by_order: dict[str, list[asyncpg.Record]] = {}
    for f in flows:
        by_order.setdefault(f["source_no"], []).append(f)

    inbound_seq: dict[str, int] = {}
    for order_no, oflows in by_order.items():
        order = await conn.fetchrow("SELECT * FROM pur_order WHERE order_no=$1", order_no)
        assert order, order_no
        new_no, min_date = PO_RENAME.get(order_no, (order_no, None))
        if min_date is None:  # 未改名：order_date 取流水最小日期对齐
            min_date = min(f["biz_time"] for f in oflows)[:10]
        max_date = max(f["biz_time"] for f in oflows)[:10]
        # 1) 数量对齐：订单行 qty := 流水 qty，并同步抬高申请行 qty
        items = await conn.fetch(
            "SELECT * FROM pur_order_item WHERE order_id=$1 ORDER BY id", order["id"]
        )
        amount_rates: list[tuple[Decimal, Decimal]] = []
        for it in items:
            flow_qty = next(
                (f["qty"] for f in oflows if f["goods_id"] == it["goods_id"]), None
            )
            if flow_qty is None:
                amount_rates.append((it["amount"], it["tax_rate"]))
                continue
            amount = (flow_qty * it["price"]).quantize(Decimal("0.01"))
            await conn.execute(
                "UPDATE pur_order_item SET qty=$1, amount=$2 WHERE id=$3",
                flow_qty, amount, it["id"],
            )
            await conn.execute(
                "UPDATE pur_apply_item SET qty=$1 WHERE id=$2 AND qty<$1",
                flow_qty, it["apply_item_id"],
            )
            amount_rates.append((amount, it["tax_rate"]))
        total = sum((a for a, _ in amount_rates), D0).quantize(Decimal("0.01"))
        # 2) 订单改号/日期/审批/金额
        await conn.execute(
            "UPDATE pur_order SET order_no=$2, order_date=$3, status=CASE WHEN status<2 "
            "THEN 2 ELSE status END, audit_by=$4, audit_at=$5, audit_remark='演示数据', "
            "total_amount=$6, tax_amount=$7 WHERE id=$1",
            order["id"], new_no, dt.date.fromisoformat(min_date), ADMIN,
            dt.date.fromisoformat(min_date), total, tax_of(amount_rates),
        )
        if new_no != order_no:  # 流水改指向新单号
            await conn.execute(
                "UPDATE inv_stock_flow SET source_no=$2 WHERE source_type='pur_order' "
                "AND source_no=$1",
                order_no, new_no,
            )
        # 3) 入库单（单号按入库日期 = 最大流水日期）
        seq = inbound_seq.get(max_date, 0) + 1
        inbound_seq[max_date] = seq
        pi_no = f"PI{max_date.replace('-', '')}{seq:04d}"
        pi_id = await conn.fetchval(
            "INSERT INTO pur_inbound (inbound_no, supplier_id, warehouse_id, order_id, "
            "inbound_date, status, total_amount, tax_amount, remark, created_at, updated_at, "
            "created_by, updated_by, tenant_id) "
            "VALUES ($1,$2,$3,$4,$5,1,$6,$7,'演示数据',now(),now(),$8,$8,1) RETURNING id",
            pi_no, order["supplier_id"], MAIN_WH, order["id"],
            dt.date.fromisoformat(max_date),
            total, tax_of(amount_rates), ADMIN,
        )
        # 4) 入库明细 + 回写 inbound_qty + 订单状态；流水改指向入库单
        n_in = 0
        for it in items:
            flow_qty = next(
                (f["qty"] for f in oflows if f["goods_id"] == it["goods_id"]), None
            )
            if flow_qty is None:
                continue
            await conn.execute(
                "INSERT INTO pur_inbound_item (inbound_id, order_item_id, goods_id, qty, "
                "price, amount, tax_rate, created_at, updated_at, created_by, updated_by, "
                "tenant_id) VALUES ($1,$2,$3,$4,$5,$6,$7,now(),now(),$8,$8,1)",
                pi_id, it["id"], it["goods_id"], flow_qty, it["price"],
                (flow_qty * it["price"]).quantize(Decimal("0.01")), it["tax_rate"], ADMIN,
            )
            await conn.execute(
                "UPDATE pur_order_item SET inbound_qty=$1 WHERE id=$2", flow_qty, it["id"]
            )
            n_in += 1
        all_in = await conn.fetchval(
            "SELECT COUNT(*) FROM pur_order_item WHERE order_id=$1 "
            "AND (inbound_qty IS NULL OR inbound_qty=0)", order["id"],
        )
        new_status = 4 if all_in == 0 else 3
        await conn.execute("UPDATE pur_order SET status=$2 WHERE id=$1",
                           order["id"], new_status)
        await conn.execute(
            "UPDATE inv_stock_flow SET source_type='pur_inbound', source_no=$2 "
            "WHERE source_type='pur_order' AND source_no=$1",
            new_no, pi_no,
        )
        print(f"采购闭环：{order_no} -> {new_no} 入库单 {pi_no} "
              f"({n_in} 行, 订单状态 {new_status})")


async def fix_sales(conn: asyncpg.Connection) -> None:
    if await conn.fetchval("SELECT COUNT(*) FROM sal_order"):
        print("销售闭环：已存在销售订单，跳过")
        return
    customers = await conn.fetch(
        "SELECT id FROM bd_customer WHERE status=1 AND is_deleted=false ORDER BY id"
    )
    for i, (old_no, order_no, outbound_no) in enumerate(SALES):
        f = await conn.fetchrow(
            "SELECT * FROM inv_stock_flow WHERE source_type='sal_order' AND source_no=$1",
            old_no,
        )
        assert f, old_no
        goods = await conn.fetchrow("SELECT * FROM bd_goods WHERE id=$1", f["goods_id"])
        date = f["biz_time"][:10]
        cust = customers[i % len(customers)]["id"]
        amount = (f["qty"] * goods["sale_price"]).quantize(Decimal("0.01"))
        tax = tax_of([(amount, TAX_RATE)])
        order_id = await conn.fetchval(
            "INSERT INTO sal_order (order_no, customer_id, order_date, status, "
            "total_amount, tax_amount, discount, audit_by, audit_at, audit_remark, remark, "
            "created_at, updated_at, created_by, updated_by, tenant_id) "
            "VALUES ($1,$2,$3,4,$4,$5,0,$6,$7,'演示数据','演示数据',now(),now(),$6,$6,1) "
            "RETURNING id",
            order_no, cust, dt.date.fromisoformat(date), amount, tax, ADMIN,
            dt.date.fromisoformat(date),
        )
        item_id = await conn.fetchval(
            "INSERT INTO sal_order_item (order_id, goods_id, unit_id, qty, price, amount, "
            "tax_rate, outbound_qty, created_at, updated_at, created_by, updated_by, "
            "tenant_id) VALUES ($1,$2,$3,$4,$5,$6,$7,$4,now(),now(),$8,$8,1) RETURNING id",
            order_id, goods["id"], goods["unit_id"], f["qty"], goods["sale_price"],
            amount, TAX_RATE, ADMIN,
        )
        out_id = await conn.fetchval(
            "INSERT INTO sal_outbound (outbound_no, customer_id, warehouse_id, order_id, "
            "outbound_date, status, total_amount, tax_amount, remark, created_at, "
            "updated_at, created_by, updated_by, tenant_id) "
            "VALUES ($1,$2,$3,$4,$5,1,$6,$7,'演示数据',now(),now(),$8,$8,1) RETURNING id",
            outbound_no, cust, f["warehouse_id"], order_id, dt.date.fromisoformat(date),
            amount, tax, ADMIN,
        )
        await conn.execute(
            "INSERT INTO sal_outbound_item (outbound_id, order_item_id, goods_id, qty, "
            "price, amount, created_at, updated_at, created_by, updated_by, tenant_id) "
            "VALUES ($1,$2,$3,$4,$5,$6,now(),now(),$7,$7,1)",
            out_id, item_id, goods["id"], f["qty"], goods["sale_price"], amount, ADMIN,
        )
        await conn.execute(
            "UPDATE inv_stock_flow SET source_type='sal_outbound', source_no=$2 "
            "WHERE source_type='sal_order' AND source_no=$1",
            old_no, outbound_no,
        )
        print(f"销售闭环：{old_no} -> {order_no} + {outbound_no} (客户 {cust})")


async def fix_check(conn: asyncpg.Connection) -> None:
    f = await conn.fetchrow(
        "SELECT * FROM inv_stock_flow WHERE source_type='inv_check' AND source_no='IC202611050001'"
    )
    if not f:
        print("盘点闭环：流水已改指向，跳过")
        return
    goods = await conn.fetchrow("SELECT * FROM bd_goods WHERE id=$1", f["goods_id"])
    date = f["biz_time"][:10]
    check_no = f"IC{date.replace('-', '')}0001"
    cost = goods["cost_price"]
    diff_amount = (f["qty"] * cost).quantize(Decimal("0.01"))
    check_id = await conn.fetchval(
        "INSERT INTO inv_check (check_no, warehouse_id, check_date, status, adjust_by, "
        "total_diff_qty, total_diff_amount, remark, created_at, updated_at, created_by, "
        "updated_by, tenant_id) VALUES ($1,$2,$3,1,$4,$5,$6,'演示数据',now(),now(),$4,$4,1) "
        "RETURNING id",
        check_no, f["warehouse_id"], dt.date.fromisoformat(date), ADMIN, f["qty"],
        diff_amount,
    )
    await conn.execute(
        "INSERT INTO inv_check_item (check_id, goods_id, book_qty, real_qty, diff_qty, "
        "cost, created_at, updated_at, created_by, updated_by, tenant_id) "
        "VALUES ($1,$2,0,$3,$3,$4,now(),now(),$5,$5,1)",
        check_id, goods["id"], f["qty"], cost, ADMIN,
    )
    await conn.execute(
        "UPDATE inv_stock_flow SET source_no=$1 WHERE source_type='inv_check' "
        "AND source_no='IC202611050001'",
        check_no,
    )
    print(f"盘点闭环：IC202611050001 -> {check_no}")


async def _create_opening_check(
    conn: asyncpg.Connection,
    check_no: str,
    warehouse_id: int,
    items: list[dict],
    remark: str,
) -> None:
    """items: [{goods_id, qty, cost}] -> 盘点单 + 明细 + check_gain 流水(book 0 -> qty)。"""
    check_id = await conn.fetchval(
        "INSERT INTO inv_check (check_no, warehouse_id, check_date, status, adjust_by, "
        "total_diff_qty, total_diff_amount, remark, created_at, updated_at, created_by, "
        "updated_by, tenant_id) VALUES ($1,$2,$3,1,$4,$5,$6,$7,now(),now(),$4,$4,1) "
        "RETURNING id",
        check_no, warehouse_id, dt.date.fromisoformat(OPENING_DATE), ADMIN,
        sum(i["qty"] for i in items),
        sum(((i["qty"] * i["cost"]).quantize(Decimal("0.01")) for i in items), D0),
        remark,
    )
    for i in items:
        await conn.execute(
            "INSERT INTO inv_check_item (check_id, goods_id, book_qty, real_qty, diff_qty, "
            "cost, created_at, updated_at, created_by, updated_by, tenant_id) "
            "VALUES ($1,$2,0,$3,$3,$4,now(),now(),$5,$5,1)",
            check_id, i["goods_id"], i["qty"], i["cost"], ADMIN,
        )
        await conn.execute(
            "INSERT INTO inv_stock_flow (tenant_id, warehouse_id, goods_id, flow_type, "
            "direction, qty, before_qty, after_qty, unit_cost, source_type, source_no, "
            "biz_time, remark, created_at, updated_at) "
            "VALUES (1,$1,$2,'check_gain',1,$3,0,$3,$4,'inv_check',$5,$6,$7,now(),now())",
            warehouse_id, i["goods_id"], i["qty"], i["cost"], check_no,
            f"{OPENING_DATE} 08:00:00+08", remark,
        )


async def fix_opening(conn: asyncpg.Connection) -> None:
    if await conn.fetchval("SELECT COUNT(*) FROM inv_check WHERE check_date=%s" % f"'{OPENING_DATE}'"):
        print("期初建账：已存在期初盘点单，跳过")
        return
    # a) 有库存但无流水的 -> 期初 = 当前库存
    orphan = await conn.fetch(
        "SELECT s.goods_id, s.qty, g.cost_price FROM inv_stock s "
        "JOIN bd_goods g ON g.id=s.goods_id "
        "LEFT JOIN inv_stock_flow f ON f.warehouse_id=s.warehouse_id AND f.goods_id=s.goods_id "
        "WHERE f.id IS NULL AND s.warehouse_id=$1",
        MAIN_WH,
    )
    # b) 首笔流水 before_qty>0 的链条 -> 期初 = 首笔 before_qty
    heads = await conn.fetch(
        "SELECT DISTINCT ON (f.goods_id) f.goods_id, f.before_qty, g.cost_price "
        "FROM inv_stock_flow f JOIN bd_goods g ON g.id=f.goods_id "
        "WHERE f.warehouse_id=$1 AND f.biz_time > $2 "
        "AND f.before_qty > 0 "
        "ORDER BY f.goods_id, f.biz_time, f.id",
        MAIN_WH, OPENING_DATE + " 23:59:59",
    )
    items = [
        {"goods_id": r["goods_id"], "qty": r["qty"], "cost": r["cost_price"]}
        for r in orphan
    ] + [
        {"goods_id": r["goods_id"], "qty": r["before_qty"], "cost": r["cost_price"]}
        for r in heads
    ]
    if items:
        await _create_opening_check(conn, "IC202609010001", MAIN_WH, items,
                                    "期初建账-主仓库")
        print(f"期初建账：主仓库 {len(items)} 条（孤儿库存 {len(orphan)} + 链头补账 {len(heads)}）")
    else:
        print("期初建账：无需处理")


async def fix_left_warehouse(conn: asyncpg.Connection) -> None:
    n = await conn.fetchval("SELECT COUNT(*) FROM inv_stock WHERE warehouse_id=$1", LEFT_WH)
    if n:
        print(f"左1仓库：已有 {n} 条库存，跳过")
        return
    goods_list = await conn.fetch(
        "SELECT id, cost_price FROM bd_goods WHERE status=1 AND is_deleted=false "
        "ORDER BY id LIMIT $1", LEFT_GOODS_N,
    )
    items = []
    for g in goods_list:
        qty = Decimal(20 + (g["id"] * 37) % 280)
        items.append({"goods_id": g["id"], "qty": qty, "cost": g["cost_price"]})
        await conn.execute(
            "INSERT INTO inv_stock (warehouse_id, goods_id, qty, locked_qty, available_qty, "
            "avg_cost, created_at, updated_at, created_by, updated_by, tenant_id) "
            "VALUES ($1,$2,$3,0,$3,$4,now(),now(),$5,$5,1)",
            LEFT_WH, g["id"], qty, g["cost_price"], ADMIN,
        )
    await _create_opening_check(conn, "IC202609010002", LEFT_WH, items, "期初建账-左1仓库")
    print(f"左1仓库：铺货 {len(items)} 条 + 期初盘点单 IC202609010002")


async def fix_chains(conn: asyncpg.Connection) -> None:
    """收尾一致性：重算全部流水链条 before/after 并同步库存（幂等，通常 0 行）。"""
    for i in range(5):
        n = await conn.execute(
            "WITH w AS (SELECT id, COALESCE(LAG(after_qty) OVER "
            "(PARTITION BY warehouse_id, goods_id ORDER BY biz_time, id), 0) new_before, "
            "direction, qty FROM inv_stock_flow), u AS (SELECT id, new_before, "
            "new_before + direction * qty new_after FROM w) "
            "UPDATE inv_stock_flow f SET before_qty=u.new_before, after_qty=u.new_after "
            "FROM u WHERE u.id=f.id AND (f.before_qty<>u.new_before "
            "OR f.after_qty<>u.new_after)"
        )
        n = int(n.split()[-1])
        if n == 0:
            break
        print(f"链条重算：第 {i + 1} 遍修正 {n} 行")
    await conn.execute(
        "UPDATE inv_stock s SET qty=f.lq, available_qty=f.lq-s.locked_qty "
        "FROM (SELECT warehouse_id, goods_id, "
        "(array_agg(after_qty ORDER BY biz_time DESC, id DESC))[1] lq FROM inv_stock_flow "
        "GROUP BY warehouse_id, goods_id) f "
        "WHERE f.warehouse_id=s.warehouse_id AND f.goods_id=s.goods_id AND s.qty<>f.lq"
    )
    print("链条重算：库存与流水已对齐")


async def main() -> None:
    conn = await asyncpg.connect(DSN)
    async with conn.transaction():
        await fix_purchase(conn)
        await fix_sales(conn)
        await fix_check(conn)
        await fix_opening(conn)
        await fix_left_warehouse(conn)
        await fix_chains(conn)
    await conn.close()
    print("全部修复完成（单事务提交）")


asyncio.run(main())
