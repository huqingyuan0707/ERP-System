"""API v1 路由汇总。"""

import logging

from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    configs,
    dicts,
    export,
    goods,
    log,
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
api_router.include_router(supplier.router)
