"""API v1 路由汇总。"""

import logging

from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    configs,
    customer,
    dept,
    dicts,
    employee,
    export,
    goods,
    inventory,
    log,
    purchase_apply,
    purchase_inbound,
    purchase_order,
    purchase_return,
    supplier,
    system,
    task,
    warehouse,
)
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.schemas.common import ApiResponse, success_response


logger = logging.getLogger("app.health")

api_router = APIRouter()


@api_router.get("/health", summary="健康检查", tags=["系统"])
async def health() -> ApiResponse[dict[str, object]]:
    """检查应用、数据库与 Redis 的连通性，返回 200 表示健康。"""
    components: dict[str, object] = {}

    # 数据库检查：执行轻量查询
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(_select_one())
        components["database"] = "up"
    except Exception as exc:  # noqa: BLE001
        logger.warning("数据库健康检查失败: %s", exc)
        components["database"] = f"down: {exc}"

    # Redis 检查：仅在配置了 REDIS_URL 时尝试，否则标记为未启用
    if settings.REDIS_URL:
        components["redis"] = await _check_redis(settings.REDIS_URL)
    else:
        components["redis"] = "not_configured"

    healthy = components.get("database") == "up"
    payload = {"status": "ok" if healthy else "degraded", "components": components}
    return success_response(payload)


async def _check_redis(redis_url: str) -> str:
    try:
        from redis.asyncio import Redis  # 懒加载，避免未安装时影响启动
    except ImportError:
        return "not_installed"
    try:
        client = Redis.from_url(redis_url, socket_timeout=2)
        try:
            await client.ping()
            return "up"
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 健康检查失败: %s", exc)
        return f"down: {exc}"


def _select_one():
    from sqlalchemy import text

    return text("SELECT 1")


api_router.include_router(auth.router)
api_router.include_router(system.router)
api_router.include_router(dicts.router)
api_router.include_router(configs.router)
# 导入导出包含 /base/goods/export、/base/goods/import 等静态路径，
# 必须先于 goods.router 注册，否则会被 /base/goods/{goods_id} 当作动态参数匹配（422）
api_router.include_router(export.router)
api_router.include_router(goods.router)
api_router.include_router(log.router)
api_router.include_router(task.router)
api_router.include_router(warehouse.router)
api_router.include_router(inventory.router)
api_router.include_router(supplier.router)
api_router.include_router(customer.router)
# B-06：/base/depts/sort、/base/employees/options 等静态路径均在各自模块内先于 /{id} 注册
api_router.include_router(dept.router)
api_router.include_router(employee.router)
# P-01：采购申请 /purchase/applies；列表与新增为静态路径，先于 /{apply_id} 注册
api_router.include_router(purchase_apply.router)
# P-02：采购订单 /purchase/orders；/orders/from-apply/{apply_id} 为静态段，先于 /orders/{order_id}
api_router.include_router(purchase_order.router)
# P-03：采购入库 /purchase/inbounds；/inbounds/from-order/{order_id} 为静态段，先于 /{inbound_id}
api_router.include_router(purchase_inbound.router)
# P-04：采购退货 /purchase/returns；/returns/source 为静态段，先于 /{return_id}
api_router.include_router(purchase_return.router)
