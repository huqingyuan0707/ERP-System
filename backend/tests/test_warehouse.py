"""仓库管理验收测试（B-03）。

覆盖：
  - 新增：编码重复 409；负责人不存在 422；
    设为默认时，同一事务内把其它仓 is_default 置 false（默认仓唯一）。
  - 修改：部分更新；改编码要查重 409；切默认先取消旧默认。
  - 停用/删除：先查 inv_stock，有库存返回 409；
    默认仓禁止停用/删除 409；删除为软删。
  - 操作日志：新增/修改/切换默认均写入 sys_oper_log。
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import httpx
from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.base_data import BdGoods, BdWarehouse
from app.models.inventory import InvStock
from app.models.sys import SysOperLog, SysUser


API = "/api/v1"
ADMIN = ("admin", "admin123")
PASSWORD = "Passw0rd123"


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


@asynccontextmanager
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _login_token(client: httpx.AsyncClient, username: str, password: str) -> str:
    resp = await client.post(f"{API}/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["data"]["accessToken"])


async def _admin_id() -> int:
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(SysUser).where(SysUser.username == "admin"))
        ).scalar_one()
        return int(user.id)


async def _goods_id() -> int:
    async with AsyncSessionLocal() as session:
        goods = (
            await session.execute(select(BdGoods).where(BdGoods.code == "GD0001"))
        ).scalar_one()
        return int(goods.id)


async def _warehouse_rows(codes: list[str]) -> dict[str, tuple[int, bool]]:
    """返回 {code: (id, is_default)}。"""
    async with AsyncSessionLocal() as session:
        stmt = select(BdWarehouse.code, BdWarehouse.id, BdWarehouse.is_default).where(
            BdWarehouse.code.in_(codes)
        )
        return {row[0]: (int(row[1]), bool(row[2])) for row in (await session.execute(stmt)).all()}


async def _create_warehouse(client: httpx.AsyncClient, token: str, payload: dict) -> httpx.Response:
    return await client.post(
        f"{API}/base/warehouses",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )


async def _cleanup(created_ids: list[int]) -> None:
    """物理删除测试创建的仓库，并先清理其库存记录（避免外键冲突）。"""
    if not created_ids:
        return
    async with AsyncSessionLocal() as session:
        await session.execute(delete(InvStock).where(InvStock.warehouse_id.in_(created_ids)))
        await session.execute(delete(BdWarehouse).where(BdWarehouse.id.in_(created_ids)))
        await session.commit()


def test_warehouse_b03_rules() -> None:
    """B-03 仓库业务规则综合验收。"""
    _run(_scenario())


async def _scenario() -> None:
    suffix = uuid.uuid4().hex[:8]
    created_ids: list[int] = []
    try:
        async with _client() as client:
            token = await _login_token(client, *ADMIN)
            headers = {"Authorization": f"Bearer {token}"}
            manager_id = await _admin_id()
            goods_id = await _goods_id()

            # ------------------------------------------------------------------
            # 1. 新增仓库成功
            # ------------------------------------------------------------------
            code_a = f"WH_TEST_{suffix}_A"
            resp = await _create_warehouse(
                client,
                token,
                {
                    "code": code_a,
                    "name": f"测试仓A{suffix}",
                    "managerId": manager_id,
                    "isDefault": False,
                    "status": 1,
                    "remark": "测试A",
                },
            )
            assert resp.status_code == 200, resp.text
            warehouse_a = resp.json()["data"]
            created_ids.append(int(warehouse_a["id"]))
            assert warehouse_a["code"] == code_a
            assert warehouse_a["managerId"] == manager_id
            assert warehouse_a["isDefault"] is False

            # ------------------------------------------------------------------
            # 2. 编码重复 -> 409
            # ------------------------------------------------------------------
            resp = await _create_warehouse(
                client,
                token,
                {"code": code_a, "name": f"重复仓{suffix}", "isDefault": False, "status": 1},
            )
            assert resp.status_code == 409, resp.text

            # ------------------------------------------------------------------
            # 3. 负责人不存在 -> 422
            # ------------------------------------------------------------------
            resp = await _create_warehouse(
                client,
                token,
                {
                    "code": f"WH_TEST_{suffix}_MGR",
                    "name": f"坏负责人仓{suffix}",
                    "managerId": 999999,
                    "isDefault": False,
                    "status": 1,
                },
            )
            assert resp.status_code == 422, resp.text

            # ------------------------------------------------------------------
            # 4. 设为默认时，其它仓 is_default 置 false（默认仓唯一）
            #    先把 A 设为默认，再新增 B 并设为默认，A 应置 false。
            # ------------------------------------------------------------------
            resp = await client.put(
                f"{API}/base/warehouses/{warehouse_a['id']}",
                headers=headers,
                json={"isDefault": True},
            )
            assert resp.status_code == 200, resp.text

            code_b = f"WH_TEST_{suffix}_B"
            resp = await _create_warehouse(
                client,
                token,
                {"code": code_b, "name": f"测试仓B{suffix}", "isDefault": True, "status": 1},
            )
            assert resp.status_code == 200, resp.text
            warehouse_b = resp.json()["data"]
            created_ids.append(int(warehouse_b["id"]))

            rows = await _warehouse_rows([code_a, code_b])
            assert rows[code_a][1] is False, f"{code_a} 应不再是默认仓"
            assert rows[code_b][1] is True, f"{code_b} 应为默认仓"

            # ------------------------------------------------------------------
            # 5. 有库存删除 -> 409
            # ------------------------------------------------------------------
            code_c = f"WH_TEST_{suffix}_C"
            resp = await _create_warehouse(
                client,
                token,
                {"code": code_c, "name": f"有库存仓{suffix}", "isDefault": False, "status": 1},
            )
            assert resp.status_code == 200, resp.text
            warehouse_c = resp.json()["data"]
            created_ids.append(int(warehouse_c["id"]))

            async with AsyncSessionLocal() as session:
                session.add(
                    InvStock(
                        tenant_id=1,  # 测试环境默认租户 ID
                        warehouse_id=warehouse_c["id"],
                        goods_id=goods_id,
                        qty=10,
                        locked_qty=0,
                        available_qty=10,
                        avg_cost=0,
                    )
                )
                await session.commit()

            resp = await client.delete(
                f"{API}/base/warehouses/{warehouse_c['id']}", headers=headers
            )
            assert resp.status_code == 409, resp.text

            # ------------------------------------------------------------------
            # 6. 默认仓禁止停用/删除 -> 409（B 当前为默认仓）
            # ------------------------------------------------------------------
            resp = await client.put(
                f"{API}/base/warehouses/{warehouse_b['id']}",
                headers=headers,
                json={"status": 0},
            )
            assert resp.status_code == 409, resp.text

            resp = await client.delete(
                f"{API}/base/warehouses/{warehouse_b['id']}", headers=headers
            )
            assert resp.status_code == 409, resp.text

            # ------------------------------------------------------------------
            # 7. 改编码查重 -> 409
            # ------------------------------------------------------------------
            code_d = f"WH_TEST_{suffix}_D"
            code_e = f"WH_TEST_{suffix}_E"
            resp = await _create_warehouse(
                client,
                token,
                {"code": code_d, "name": f"仓D{suffix}", "isDefault": False, "status": 1},
            )
            assert resp.status_code == 200, resp.text
            warehouse_d = resp.json()["data"]
            created_ids.append(int(warehouse_d["id"]))

            resp = await _create_warehouse(
                client,
                token,
                {"code": code_e, "name": f"仓E{suffix}", "isDefault": False, "status": 1},
            )
            assert resp.status_code == 200, resp.text
            warehouse_e = resp.json()["data"]
            created_ids.append(int(warehouse_e["id"]))

            resp = await client.put(
                f"{API}/base/warehouses/{warehouse_d['id']}",
                headers=headers,
                json={"code": code_e},
            )
            assert resp.status_code == 409, resp.text

            # ------------------------------------------------------------------
            # 8. 切默认：部分更新把默认从 B 切到 D（同时改 code）
            #    B 应置 false，D 应成为默认仓；写入“切换默认仓”日志。
            # ------------------------------------------------------------------
            code_d2 = f"WH_TEST_{suffix}_D2"
            resp = await client.put(
                f"{API}/base/warehouses/{warehouse_d['id']}",
                headers=headers,
                json={"isDefault": True, "code": code_d2},
            )
            assert resp.status_code == 200, resp.text

            rows = await _warehouse_rows([code_b, code_d2])
            assert rows[code_b][1] is False, f"{code_b} 应不再是默认仓"
            assert rows[code_d2][1] is True, f"{code_d2} 应成为默认仓"

            # ------------------------------------------------------------------
            # 9. 软删除：无库存、非默认仓可删除，is_deleted=True
            # ------------------------------------------------------------------
            code_f = f"WH_TEST_{suffix}_F"
            resp = await _create_warehouse(
                client,
                token,
                {"code": code_f, "name": f"可删除仓{suffix}", "isDefault": False, "status": 1},
            )
            assert resp.status_code == 200, resp.text
            warehouse_f = resp.json()["data"]
            created_ids.append(int(warehouse_f["id"]))

            resp = await client.delete(
                f"{API}/base/warehouses/{warehouse_f['id']}", headers=headers
            )
            assert resp.status_code == 200, resp.text

            rows = await _warehouse_rows([code_f])
            # 软删除后查询列表不过滤 is_deleted（build_query 过滤 is_deleted=False），
            # 所以这里用 ORM 直接判断 is_deleted。
            async with AsyncSessionLocal() as session:
                orm = (
                    await session.execute(
                        select(BdWarehouse).where(BdWarehouse.code == code_f)
                    )
                ).scalar_one()
                assert orm.is_deleted is True, "删除应为软删"

            # ------------------------------------------------------------------
            # 10. 操作日志：新增/修改/切换默认均有记录
            # ------------------------------------------------------------------
            async with AsyncSessionLocal() as session:
                logs = (
                    await session.execute(
                        select(SysOperLog).where(
                            SysOperLog.module == "基础资料",
                            SysOperLog.action.in_(
                                ["新增仓库", "修改仓库", "切换默认仓"]
                            ),
                        )
                    )
                ).scalars().all()
            actions = {log.action for log in logs}
            assert "新增仓库" in actions, "缺少新增仓库操作日志"
            assert "修改仓库" in actions, "缺少修改仓库操作日志"
            assert "切换默认仓" in actions, "缺少切换默认仓操作日志"

    finally:
        await _cleanup(created_ids)
