"""系统健康检查API。"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.maintenance import MaintenanceService

router = APIRouter(prefix="/health", tags=["健康检查"])


@router.get("/")
async def health_check():
    """基础健康检查接口。"""
    return {"status": "healthy"}


@router.get("/detailed")
async def detailed_health_check(db: AsyncSession = Depends(get_db)):
    """详细健康检查，包括数据库连接池和数据统计。"""
    maintenance = MaintenanceService(db)
    health = await maintenance.get_system_health()
    return health
