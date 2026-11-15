"""P-02 采购订单状态机与验收测试（HTTP 层）。

覆盖《企业ERP系统简易需求说明书》P-02 验收清单：
  1. 2 行明细 10×100（13%）、5×50（0%）→ 合计 1250、税额 115.04；
  2. 草稿可编辑；提交后待审；审核通过已审；驳回回草稿并记录原因；
  3. 已审后编辑返回 409；关闭后状态「关闭」；
  4. 版本冲突 409（携带陈旧版本号 / 并发修改）；
  5. 数据范围、打印数据（详情快照）、Excel 导出、操作日志完整。

复用 test_purchase_order.py 的夹具与清理函数，保持与既有基线一致的写法。
"""

import asyncio
import uuid
from datetime import date
from decimal import Decimal
from io import BytesIO

import httpx
from openpyxl import load_workbook
from sqlalchemy import func, select

from app.db.session import AsyncSessionLocal
from app.models.finance import FinPayable, FinReceivable
from app.models.inventory import InvStock, InvStockFlow
from app.models.sys import SysOperLog
from tests.test_purchase_order import (
    ADMIN,
    API,
    PASSWORD,
    _auth,
    _cleanup,
    _client,
    _create_dept,
    _create_role_user,
    _login_token,
    _pick_seed,
    _run,
)


async def _create_order(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    supplier_id: int,
    goods_a: int,
    goods_b: int,
    unit_id: int,
) -> dict:
    """建一张验收用订单：10×100（13%）+ 5×50（0%），合计 1250、税额 115.04。"""
    resp = await client.post(
        f"{API}/purchase/orders",
        headers=headers,
        json={
            "supplierId": supplier_id,
            "orderDate": date.today().isoformat(),
            "remark": "P-02 验收订单",
            "items": [
                {
                    "goodsId": goods_a,
                    "unitId": unit_id,
                    "qty": "10",
                    "price": "100",
                    "taxRate": "0.13",
                },
                {
                    "goodsId": goods_b,
                    "unitId": unit_id,
                    "qty": "5",
                    "price": "50",
                    "taxRate": "0",
                },
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    return dict(resp.json()["data"])


async def _assert_no_stock_or_finance_written() -> None:
    """订单全流程不得写库存与财务；用全表计数为 0 断言（与其它采购测试口径一致）。"""
    async with AsyncSessionLocal() as session:
        for model in (InvStockFlow, InvStock, FinReceivable, FinPayable):
            count = await session.scalar(select(func.count()).select_from(model))
            assert int(count or 0) == 0, f"采购订单不应写入 {model.__tablename__}"


async def _workflow_scenario() -> None:
    """验收 1/2/3/5：金额口径、状态机、驳回原因、关闭、详情、导出、日志。"""
    suffix = uuid.uuid4().hex[:8]
    dept_id = await _create_dept(f"P02流程部{suffix}")
    goods_a, goods_b, unit_id, supplier_id = await _pick_seed()
    order_ids: list[int] = []
    apply_ids: list[int] = []
    dept_ids = [dept_id]
    supplier_ids = [supplier_id]
    user_ids: list[int] = []
    role_ids: list[int] = []

    async with _client() as client:
        headers = _auth(await _login_token(client, *ADMIN))
        try:
            # --- 验收 1：金额与税额（含税口径，银行家舍入）---
            order = await _create_order(
                client, headers, supplier_id, goods_a, goods_b, unit_id
            )
            order_id = int(order["id"])
            order_ids.append(order_id)
            assert order["status"] == 0, "新建订单应为草稿"
            assert Decimal(str(order["totalAmount"])) == Decimal("1250.00")
            assert Decimal(str(order["taxAmount"])) == Decimal("115.04")
            assert order["itemsCount"] == 2
            assert Decimal(str(order["totalQty"])) == Decimal("15")
            assert Decimal(str(order["inboundQty"])) == Decimal("0")
            assert Decimal(str(order["inboundProgress"])) == Decimal("0")
            version = int(order["version"])

            # 详情（修复后应返回完整明细 + 累计入库数量，供打印取快照）
            resp = await client.get(f"{API}/purchase/orders/{order_id}", headers=headers)
            assert resp.status_code == 200, resp.text
            detail = resp.json()["data"]
            assert detail["id"] == order_id
            assert len(detail["items"]) == 2
            assert all(Decimal(str(i["inboundQty"])) == 0 for i in detail["items"])
            assert all(Decimal(str(i["remainingQty"])) > 0 for i in detail["items"])

            # --- 验收 2：草稿可编辑，版本号推进 ---
            resp = await client.put(
                f"{API}/purchase/orders/{order_id}",
                headers=headers,
                json={"remark": "草稿修改", "version": version},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["remark"] == "草稿修改"
            version = int(resp.json()["data"]["version"])

            # 提交：草稿 → 待审；重复提交 409
            resp = await client.post(
                f"{API}/purchase/orders/{order_id}/submit?version={version}", headers=headers
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["status"] == 1
            version = int(resp.json()["data"]["version"])
            resp = await client.post(
                f"{API}/purchase/orders/{order_id}/submit", headers=headers
            )
            assert resp.status_code == 409, "重复提交应被拒绝"

            # 非草稿编辑 409
            resp = await client.put(
                f"{API}/purchase/orders/{order_id}",
                headers=headers,
                json={"remark": "待审不可改"},
            )
            assert resp.status_code == 409, resp.text

            # 驳回：原因必填（422）；驳回后回草稿并记录原因
            resp = await client.post(
                f"{API}/purchase/orders/{order_id}/audit",
                headers=headers,
                json={"result": "rejected"},
            )
            assert resp.status_code == 422, "驳回必须填写原因"
            resp = await client.post(
                f"{API}/purchase/orders/{order_id}/audit",
                headers=headers,
                json={"result": "rejected", "remark": "单价偏高，请重新询价"},
            )
            assert resp.status_code == 200, resp.text
            rejected = resp.json()["data"]
            assert rejected["status"] == 0
            assert rejected["auditRemark"] == "单价偏高，请重新询价"
            version = int(rejected["version"])

            # 重新提交清空上一次驳回原因，再审核通过
            resp = await client.post(
                f"{API}/purchase/orders/{order_id}/submit?version={version}", headers=headers
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["auditRemark"] is None
            version = int(resp.json()["data"]["version"])
            resp = await client.post(
                f"{API}/purchase/orders/{order_id}/audit",
                headers=headers,
                json={"result": "approved", "remark": "同意采购", "version": version},
            )
            assert resp.status_code == 200, resp.text
            approved = resp.json()["data"]
            assert approved["status"] == 2
            assert approved["auditByName"]
            version = int(approved["version"])

            # --- 验收 3：已审后编辑返回 409 ---
            resp = await client.put(
                f"{API}/purchase/orders/{order_id}",
                headers=headers,
                json={"remark": "已审不可改", "version": version},
            )
            assert resp.status_code == 409, resp.text

            # 关闭：状态 5、原因落库、重复关闭 409
            resp = await client.post(
                f"{API}/purchase/orders/{order_id}/close",
                headers=headers,
                json={"reason": "供应商停产，剩余数量作废", "version": version},
            )
            assert resp.status_code == 200, resp.text
            closed = resp.json()["data"]
            assert closed["status"] == 5
            assert closed["auditRemark"] == "供应商停产，剩余数量作废"
            resp = await client.post(
                f"{API}/purchase/orders/{order_id}/close",
                headers=headers,
                json={"reason": "再次关闭"},
            )
            assert resp.status_code == 409, "重复关闭应被拒绝"

            # 关闭后详情：剩余可入库数量作废（展示 0），打印取单据快照
            resp = await client.get(f"{API}/purchase/orders/{order_id}", headers=headers)
            assert resp.status_code == 200, resp.text
            assert all(
                Decimal(str(i["remainingQty"])) == 0
                for i in resp.json()["data"]["items"]
            )

            # --- 验收 5：Excel 导出（report:export）、操作日志、不写库存财务 ---
            resp = await client.get(f"{API}/purchase/orders/export", headers=headers)
            assert resp.status_code == 200, resp.text
            assert "spreadsheetml" in resp.headers["content-type"]
            workbook = load_workbook(BytesIO(resp.content))
            sheet = workbook.active
            assert sheet["A1"].value == "订单号"
            assert any(sheet.cell(row=r, column=1).value == order["orderNo"] for r in range(2, 20))

            async with AsyncSessionLocal() as session:
                actions = set(
                    (
                        await session.execute(
                            select(SysOperLog.action).where(
                                SysOperLog.module == "采购管理", SysOperLog.status == 1
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            for expected in (
                "新增采购订单",
                "修改采购订单",
                "提交采购订单",
                "驳回采购订单",
                "审核通过采购订单",
                "关闭采购订单",
                "导出采购订单",
            ):
                assert expected in actions, f"缺少操作日志: {expected}"

            await _assert_no_stock_or_finance_written()
        finally:
            await _cleanup(order_ids, apply_ids, dept_ids, supplier_ids, user_ids, role_ids)


def test_purchase_order_workflow() -> None:
    """验收 1/2/3/5：金额口径、状态机、驳回、关闭、导出、日志、不写库存财务。"""
    _run(_workflow_scenario())


async def _version_conflict_scenario() -> None:
    """验收 4：两个请求持有同一版本号时，后提交者返回 409。"""
    suffix = uuid.uuid4().hex[:8]
    dept_id = await _create_dept(f"P02并发部{suffix}")
    goods_a, goods_b, unit_id, supplier_id = await _pick_seed()
    order_ids: list[int] = []
    apply_ids: list[int] = []
    dept_ids = [dept_id]
    supplier_ids = [supplier_id]
    user_ids: list[int] = []
    role_ids: list[int] = []

    async with _client() as client:
        headers = _auth(await _login_token(client, *ADMIN))
        try:
            order = await _create_order(
                client, headers, supplier_id, goods_a, goods_b, unit_id
            )
            order_id = int(order["id"])
            order_ids.append(order_id)
            version = int(order["version"])

            # 请求 A 先成功推进版本；请求 B 仍持有旧版本号（模拟并发中的落后请求）
            resp_a = await client.put(
                f"{API}/purchase/orders/{order_id}",
                headers={**headers, "Idempotency-Key": f"p02-a-{suffix}"},
                json={"remark": "请求A", "version": version},
            )
            assert resp_a.status_code == 200, resp_a.text
            assert int(resp_a.json()["data"]["version"]) == version + 1
            resp_b = await client.put(
                f"{API}/purchase/orders/{order_id}",
                headers={**headers, "Idempotency-Key": f"p02-b-{suffix}"},
                json={"remark": "请求B（陈旧版本）", "version": version},
            )
            assert resp_b.status_code == 409, resp_b.text
            assert "数据已被他人修改" in resp_b.json()["message"]

            # 真正并发：两个请求同时携带同一版本号，只允许一个成功
            current = int(resp_a.json()["data"]["version"])
            responses = await asyncio.gather(
                client.put(
                    f"{API}/purchase/orders/{order_id}",
                    headers={**headers, "Idempotency-Key": f"p02-c1-{suffix}"},
                    json={"remark": "并发1", "version": current},
                ),
                client.put(
                    f"{API}/purchase/orders/{order_id}",
                    headers={**headers, "Idempotency-Key": f"p02-c2-{suffix}"},
                    json={"remark": "并发2", "version": current},
                ),
            )
            statuses = sorted(resp.status_code for resp in responses)
            assert 409 in statuses, f"并发修改应触发版本冲突: {statuses}"
            assert statuses.count(200) <= 1, f"并发修改只能有一个成功: {statuses}"
        finally:
            await _cleanup(order_ids, apply_ids, dept_ids, supplier_ids, user_ids, role_ids)


def test_purchase_order_version_conflict() -> None:
    """验收 4：陈旧版本号与并发修改均返回 409。"""
    _run(_version_conflict_scenario())


async def _data_scope_scenario() -> None:
    """验收 5：数据范围本人/全部；越权详情 403；导出权限 report:export。"""
    suffix = uuid.uuid4().hex[:8]
    dept_id = await _create_dept(f"P02范围部{suffix}")
    goods_a, goods_b, unit_id, supplier_id = await _pick_seed()
    order_ids: list[int] = []
    apply_ids: list[int] = []
    dept_ids = [dept_id]
    supplier_ids = [supplier_id]
    user_ids: list[int] = []
    role_ids: list[int] = []

    async with _client() as client:
        admin_headers = _auth(await _login_token(client, *ADMIN))
        try:
            # admin 建的单（创建人不是受限用户）
            admin_order = await _create_order(
                client, admin_headers, supplier_id, goods_a, goods_b, unit_id
            )
            order_ids.append(int(admin_order["id"]))

            # 受限用户：本人范围 + 订单全套按钮权限（不含导出）
            username = f"p02self{suffix}"
            role_id, user_id = await _create_role_user(
                username=username,
                dept_id=dept_id,
                perm_codes=[
                    "pur:order:list",
                    "pur:order:add",
                    "pur:order:edit",
                    "pur:order:submit",
                    "pur:order:audit",
                    "pur:order:close",
                    "rpt:dashboard:list",
                ],
                data_scope=1,
            )
            role_ids.append(role_id)
            user_ids.append(user_id)
            headers = _auth(await _login_token(client, username, PASSWORD))

            # 受限用户自建一张单
            own = await _create_order(
                client, headers, supplier_id, goods_a, goods_b, unit_id
            )
            order_ids.append(int(own["id"]))

            resp = await client.get(f"{API}/purchase/orders", headers=headers)
            assert resp.status_code == 200, resp.text
            nos = {row["orderNo"] for row in resp.json()["data"]["items"]}
            assert own["orderNo"] in nos
            assert admin_order["orderNo"] not in nos, "本人范围不应看到他人订单"

            # 直接改 URL 访问他人详情 / 提交他人订单：403
            resp = await client.get(
                f"{API}/purchase/orders/{admin_order['id']}", headers=headers
            )
            assert resp.status_code == 403, resp.text
            resp = await client.post(
                f"{API}/purchase/orders/{admin_order['id']}/submit", headers=headers
            )
            assert resp.status_code == 403, "越权写操作应被数据范围拦截"

            # 无 report:export 时导出 403
            resp = await client.get(f"{API}/purchase/orders/export", headers=headers)
            assert resp.status_code == 403, "导出需要 report:export"

            # 导出只输出当前数据范围内的数据
            resp = await client.get(f"{API}/purchase/orders/export", headers=admin_headers)
            assert resp.status_code == 200, resp.text
            sheet = load_workbook(BytesIO(resp.content)).active
            exported = {
                sheet.cell(row=r, column=1).value
                for r in range(2, sheet.max_row + 1)
                if sheet.cell(row=r, column=1).value
            }
            assert own["orderNo"] in exported and admin_order["orderNo"] in exported
        finally:
            await _cleanup(order_ids, apply_ids, dept_ids, supplier_ids, user_ids, role_ids)


def test_purchase_order_data_scope() -> None:
    """验收 5：数据范围、越权 403、导出权限与范围。"""
    _run(_data_scope_scenario())
