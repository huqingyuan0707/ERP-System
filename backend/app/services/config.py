"""系统参数服务：查询、批量保存（含单号规则校验）、缓存失效。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete, cache_get, cache_set
from app.core.exceptions import AppException, ErrorCode
from app.models.sys import SysConfig
from app.repositories.config import ConfigRepository
from app.schemas.config import ConfigUpdateItem
from app.utils.bill_no import BILLNO_KEY_PREFIX, parse_billno_rule

CONFIG_CACHE_PREFIX = "cfg:"
CONFIG_CACHE_ALL = "configs:all"


class ConfigService:
    """系统参数业务逻辑。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = ConfigRepository(db)

    async def list_all(self) -> list[SysConfig]:
        """全部参数（维护页/前端 configStore）。"""
        return await self._repo.list_all()

    async def get_value(self, key: str, default: str | None = None) -> str | None:
        """读取单个参数值（带缓存），缺失返回默认值。"""
        cache_key = f"{CONFIG_CACHE_PREFIX}{key}"
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached or default
        entity = await self._repo.get_by_key(key)
        value = entity.value if entity is not None else None
        await cache_set(cache_key, value or "")
        return value if value not in (None, "") else default

    async def batch_update(self, items: list[ConfigUpdateItem]) -> list[SysConfig]:
        """批量保存参数；billno.* 键校验单号规则格式，保存后失效缓存。"""
        for item in items:
            if item.key.startswith(BILLNO_KEY_PREFIX):
                parse_billno_rule(item.value, item.key.removeprefix(BILLNO_KEY_PREFIX))
        updated: list[SysConfig] = []
        for item in items:
            entity = await self._repo.get_by_key(item.key)
            if entity is None:
                raise AppException(ErrorCode.NOT_FOUND, f"参数 {item.key} 不存在")
            entity.value = item.value
            updated.append(entity)
        await self._db.commit()
        for entity in updated:
            await cache_delete(f"{CONFIG_CACHE_PREFIX}{entity.key}")
        await cache_delete(CONFIG_CACHE_ALL)
        return updated
