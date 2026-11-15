"""库存只读查询：GET /inventory/stocks、/stocks/summary、/flows。

覆盖点：
  1. 分页与总数正确；
  2. 租户隔离：异租户库存行不可见；
  3. 软删过滤：软删商品/仓库/库存行不返回；
  4. 流水表无 is_deleted：只按 biz_time 与筛选条件过滤；
  5. 金额展示口径：stockAmount = ROUND(qty * avg_cost, 2)；
  6. 参数校验：不存在仓库返回 422，时间区间倒置/超跨度返回 422。
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.endpoints import inventory as inventory_endpoint
from app.core.deps import get_current_tenant, get_current_user, get_db
from app.core.exceptions import register_exception_handlers
from app.db.base import Base
from app.models.base_data import BdGoods, BdUnit, BdWarehouse
from app.models.inventory import InvStock, InvStockFlow
from app.models.sys import SysUser
from app.schemas.inventory import FlowQuery, StockQuery
from app.services.auth import AuthService
from app.services.inventory import InventoryService


@asynccontextmanager
async def memory_db():
    """独立内存库：表结构来自 ORM metadata，与生产模型保持一致。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        # 基础档案：本租户 + 异租户 + 软删，用于验证隔离与过滤
        db.add_all(
            [
                BdUnit(id=1, tenant_id=1, name="件", precision=0),
                BdWarehouse(id=1, tenant_id=1, code="WH1", name="主仓"),
                BdWarehouse(id=2, tenant_id=2, code="WH2", name="异租户仓"),
                BdWarehouse(id=3, tenant_id=1, code="WH3", name="删除仓", is_deleted=True),
            ]
        )
        await db.flush()
        db.add_all(
            [
                BdGoods(
                    id=1,
                    tenant_id=1,
                    code="G001",
                    name="螺丝",
                    unit_id=1,
                    stock_lower=Decimal("10"),
                    barcode="690001",
                ),
                BdGoods(
                    id=2,
                    tenant_id=1,
                    code="G002",
                    name="螺母",
                    unit_id=1,
                    # 下限 10（与库存 5 比较后为 True，用于验证 isBelowLower 与 belowLower 过滤）
                    stock_lower=Decimal("10"),
                ),
                BdGoods(id=3, tenant_id=2, code="G003", name="异租户商品"),
                BdGoods(id=4, tenant_id=1, code="G004", name="删除商品", is_deleted=True),
            ]
        )
        await db.flush()
        await _seed_stocks_and_flows(db)
        await db.commit()
        yield db
    await engine.dispose()


async def _seed_stocks_and_flows(db) -> None:
    """写入库存行与流水：本租户两条库存、一条软删库存、一条异租户库存；三条流水。"""
    db.add_all(
        [
            # 本租户：数量 100、锁定 30、均价 2.5 → 库存金额 250.00
            InvStock(
                id=1,
                tenant_id=1,
                warehouse_id=1,
                goods_id=1,
                qty=Decimal("100"),
                locked_qty=Decimal("30"),
                available_qty=Decimal("70"),
                avg_cost=Decimal("2.5"),
            ),
            # 本租户：数量 5，低于商品下限 10 → isBelowLower 为 True
            InvStock(
                id=2,
                tenant_id=1,
                warehouse_id=1,
                goods_id=2,
                qty=Decimal("5"),
                locked_qty=Decimal("0"),
                available_qty=Decimal("5"),
                avg_cost=Decimal("1"),
            ),
            # 异租户：不得出现在租户 1 的结果中
            InvStock(
                id=3,
                tenant_id=2,
                warehouse_id=2,
                goods_id=3,
                qty=Decimal("9"),
                locked_qty=Decimal("0"),
                available_qty=Decimal("9"),
                avg_cost=Decimal("1"),
            ),
            # 软删库存行：不应出现在结果中
            InvStock(
                tenant_id=1,
                warehouse_id=1,
                goods_id=4,
                qty=Decimal("1"),
                locked_qty=Decimal("0"),
                available_qty=Decimal("1"),
                avg_cost=Decimal("1"),
                is_deleted=True,
            ),
        ]
    )
    await db.flush()
    base_time = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    db.add_all(
        [
            # 收购入库：金额 100 × 2.5 = 250.00
            InvStockFlow(
                tenant_id=1,
                warehouse_id=1,
                goods_id=1,
                flow_type="purchase_in",
                direction=1,
                qty=Decimal("100"),
                before_qty=Decimal("0"),
                after_qty=Decimal("100"),
                unit_cost=Decimal("2.5"),
                source_type="purchase_in",
                source_no="PI202611010001",
                biz_time=base_time,
            ),
            # 销售出库：次日业务时间，金额 10 × 2.5 = 25.00
            InvStockFlow(
                tenant_id=1,
                warehouse_id=1,
                goods_id=1,
                flow_type="sale_out",
                direction=-1,
                qty=Decimal("10"),
                before_qty=Decimal("100"),
                after_qty=Decimal("90"),
                unit_cost=Decimal("2.5"),
                source_type="sale_out",
                source_no="OUT202611010001",
                biz_time=base_time + timedelta(days=1),
            ),
            # 异租户流水：不得出现在租户 1 的结果中
            InvStockFlow(
                tenant_id=2,
                warehouse_id=2,
                goods_id=3,
                flow_type="purchase_in",
                direction=1,
                qty=Decimal("9"),
                before_qty=Decimal("0"),
                after_qty=Decimal("9"),
                unit_cost=Decimal("1"),
                biz_time=base_time,
            ),
        ]
    )
    await db.flush()


@pytest.fixture(autouse=True)
def _grant_all_perms(monkeypatch):
    """所有用例授予通配权限：权限码本身已由 init_db 播种，这里只验证接口行为。"""

    async def _codes(self, user_id: int, tenant_id: int) -> set[str]:
        return {"*"}

    monkeypatch.setattr(AuthService, "get_permission_codes", _codes)



def _client(db) -> AsyncClient:
    """构造只挂库存路由的测试应用：覆盖基础鉴权依赖，绕过真实 JWT。

    注意两点：
      1. require_perm(code) 每次调用都会生成新的闭包函数对象，无法直接作为
         dependency_overrides 的 key，因此覆盖它的上游 get_current_user；
      2. 覆盖后 require_perm 仍会查询权限码，这里在用例内 monkeypatch
         AuthService.get_permission_codes 返回通配 "*"，避免依赖种子数据。
    """
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(inventory_endpoint.router, prefix="/api/v1")
    fake_user = SysUser(id=1, tenant_id=1, username="tester", password_hash="x")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_tenant] = lambda: 1
    app.dependency_overrides[get_current_user] = lambda: fake_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

def test_list_stocks_tenant_and_soft_delete_isolation():
    """列表只含本租户未删除库存行，金额与下限标记正确。"""

    async def scenario():
        async with memory_db() as db, _client(db) as client:
            response = await client.get("/api/v1/inventory/stocks")
            assert response.status_code == 200
            body = response.json()
            assert body["code"] == 0
            data = body["data"]
            # 异租户行与软删行都被排除，只剩 2 行
            assert data["total"] == 2
            # 主键倒序：id=2 在前
            first, second = data["items"]
            assert second["goodsCode"] == "G001"
            assert Decimal(second["qty"]) == Decimal("100")
            assert Decimal(second["availableQty"]) == Decimal("70")
            # 金额口径：100 × 2.5 = 250.00
            assert Decimal(second["stockAmount"]) == Decimal("250.00")
            assert second["warehouseName"] == "主仓"
            assert second["unitName"] == "件"
            assert second["isBelowLower"] is False
            # id=2：数量 5 低于下限 10
            assert first["isBelowLower"] is True

    asyncio.run(scenario())


def test_list_stocks_filters():
    """belowLower / onlyAvailable / keyword / goodsId 过滤生效。"""

    async def scenario():
        async with memory_db() as db, _client(db) as client:
            below = (await client.get("/api/v1/inventory/stocks?belowLower=true")).json()
            assert below["data"]["total"] == 1
            assert below["data"]["items"][0]["goodsCode"] == "G002"

            available = (await client.get("/api/v1/inventory/stocks?onlyAvailable=true")).json()
            assert available["data"]["total"] == 2

            keyword = (await client.get("/api/v1/inventory/stocks?keyword=690001")).json()
            assert keyword["data"]["total"] == 1
            assert keyword["data"]["items"][0]["goodsCode"] == "G001"

            by_goods = (await client.get("/api/v1/inventory/stocks?goodsId=2")).json()
            assert by_goods["data"]["total"] == 1
            assert by_goods["data"]["items"][0]["goodsName"] == "螺母"

    asyncio.run(scenario())


def test_list_stocks_pagination():
    """分页参数生效，总数不受分页影响。"""

    async def scenario():
        async with memory_db() as db, _client(db) as client:
            page1 = (await client.get("/api/v1/inventory/stocks?page=1&pageSize=1")).json()
            assert page1["data"]["total"] == 2
            assert len(page1["data"]["items"]) == 1
            page2 = (await client.get("/api/v1/inventory/stocks?page=2&pageSize=1")).json()
            assert page2["data"]["total"] == 2
            assert len(page2["data"]["items"]) == 1
            assert page1["data"]["items"][0]["id"] != page2["data"]["items"][0]["id"]

    asyncio.run(scenario())


def test_stock_summary_matches_list():
    """汇总口径与列表一致：数量 105、锁定 30、可用 75、金额 255。"""

    async def scenario():
        async with memory_db() as db, _client(db) as client:
            body = (await client.get("/api/v1/inventory/stocks/summary")).json()
            data = body["data"]
            assert data["lineCount"] == 2
            assert Decimal(data["totalQty"]) == Decimal("105")
            assert Decimal(data["totalLockedQty"]) == Decimal("30")
            assert Decimal(data["totalAvailableQty"]) == Decimal("75")
            assert Decimal(data["totalStockAmount"]) == Decimal("255")

    asyncio.run(scenario())

def test_list_stocks_unknown_warehouse_returns_422():
    """不存在的仓库 ID 明确报错，而不是静默返回空列表。"""

    async def scenario():
        async with memory_db() as db, _client(db) as client:
            # 异租户仓库同样视为不可访问
            response = await client.get("/api/v1/inventory/stocks?warehouseId=2")
            assert response.status_code == 422
            assert response.json()["code"] == 422

    asyncio.run(scenario())


def test_list_flows_isolation_and_order():
    """流水只返回本租户数据，按业务时间倒序，金额按 2 位精度展示。"""

    async def scenario():
        async with memory_db() as db, _client(db) as client:
            body = (await client.get("/api/v1/inventory/flows")).json()
            data = body["data"]
            assert data["total"] == 2
            newest, oldest = data["items"]
            assert newest["flowType"] == "sale_out"
            assert newest["direction"] == -1
            assert Decimal(newest["amount"]) == Decimal("25.00")
            assert oldest["sourceNo"] == "PI202611010001"
            # 流水无 is_deleted 字段，不应因软删过滤漏数据
            assert oldest["goodsCode"] == "G001"

    asyncio.run(scenario())


def test_list_flows_filters_and_date_range():
    """类型 / 方向 / 来源单号 / 日期区间过滤生效。"""

    async def scenario():
        async with memory_db() as db, _client(db) as client:
            only_out = (await client.get("/api/v1/inventory/flows?direction=-1")).json()
            assert only_out["data"]["total"] == 1

            by_type = (await client.get("/api/v1/inventory/flows?flowType=purchase_in")).json()
            assert by_type["data"]["total"] == 1

            by_source = (
                await client.get("/api/v1/inventory/flows?sourceNo=OUT202611010001")
            ).json()
            assert by_source["data"]["total"] == 1

            # 2026-11-01 当天只含 1 条（另一条在 11-02）
            one_day = (
                await client.get(
                    "/api/v1/inventory/flows?startDate=2026-11-01&endDate=2026-11-01"
                )
            ).json()
            assert one_day["data"]["total"] == 1
            assert one_day["data"]["items"][0]["flowType"] == "purchase_in"

            # 覆盖 11-02：证明 endDate 含当日全天（左闭右开实现）
            two_days = (
                await client.get(
                    "/api/v1/inventory/flows?startDate=2026-11-01&endDate=2026-11-02"
                )
            ).json()
            assert two_days["data"]["total"] == 2

    asyncio.run(scenario())


def test_query_schema_rejects_invalid_params():
    """区间倒置、超跨度、非法 ID 在 Schema 层即被拒绝。"""
    with pytest.raises(ValidationError):
        FlowQuery(start_date=datetime(2026, 11, 5).date(), end_date=datetime(2026, 11, 1).date())
    with pytest.raises(ValidationError):
        FlowQuery(start_date=datetime(2024, 1, 1).date(), end_date=datetime(2026, 1, 1).date())
    with pytest.raises(ValidationError):
        StockQuery(warehouse_id=0)


def test_service_direct_query_returns_joined_names():
    """服务层一次查询即可带出关联名称，业务层无需逐行回查档案表。"""

    async def scenario():
        async with memory_db() as db:
            service = InventoryService(db, tenant_id=1)
            result = await service.list_stocks(StockQuery(page=1, page_size=20))
            assert result.total == 2
            assert len(result.items) == 2
            assert {item.warehouse_name for item in result.items} == {"主仓"}

    asyncio.run(scenario())

