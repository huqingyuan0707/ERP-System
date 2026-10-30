"""字典服务：分组查询（带缓存）、类型/字典项维护、缓存失效。"""

import json

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete, cache_delete_prefix, cache_get, cache_set
from app.core.exceptions import AppException, ErrorCode
from app.models.sys import SysDictItem, SysDictType
from app.repositories.dict import DictRepository
from app.schemas.dict import (
    DictItemCreate,
    DictItemUpdate,
    DictTypeCreate,
    DictTypeUpdate,
)


DICT_CACHE_ALL = "dicts:all"
DICT_CACHE_PREFIX = "dict:"


class DictService:
    """字典业务逻辑。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = DictRepository(db)

    async def get_grouped(self) -> dict[str, list[dict[str, str]]]:
        """全量启用字典，按类型分组（缓存 dicts:all）。"""
        cached = await cache_get(DICT_CACHE_ALL)
        if cached is not None:
            data: dict[str, list[dict[str, str]]] = json.loads(cached)
            return data

        types = await self._repo.list_types()
        enabled_codes = {t.code for t in types if t.status == 1}
        items = await self._repo.list_all_enabled_items()
        grouped: dict[str, list[dict[str, str]]] = {}
        for item in items:
            if item.type_code not in enabled_codes:
                continue
            grouped.setdefault(item.type_code, []).append(
                {"label": item.label, "value": item.value}
            )
        await cache_set(DICT_CACHE_ALL, json.dumps(grouped, ensure_ascii=False))
        return grouped

    async def list_types(self) -> list[SysDictType]:
        """维护页：全部字典类型（含停用）。"""
        return await self._repo.list_types()

    async def list_items(self, type_code: str) -> list[SysDictItem]:
        """维护页：指定类型全部字典项（含停用）。"""
        return await self._repo.list_items(type_code)

    async def create_type(self, payload: DictTypeCreate) -> SysDictType:
        """新增字典类型。"""
        if await self._repo.get_type_by_code(payload.code) is not None:
            raise AppException(ErrorCode.CONFLICT, f"字典类型编码 {payload.code} 已存在")
        entity = SysDictType(
            code=payload.code,
            name=payload.name,
            status=payload.status,
            remark=payload.remark,
        )
        self._db.add(entity)
        try:
            await self._db.commit()
        except IntegrityError as exc:
            await self._db.rollback()
            raise AppException(ErrorCode.CONFLICT, f"字典类型编码 {payload.code} 已存在") from exc
        await cache_delete(DICT_CACHE_ALL)
        return entity

    async def update_type(self, type_id: int, payload: DictTypeUpdate) -> SysDictType:
        """修改字典类型（部分更新，编码不可改）。"""
        entity = await self._repo.get_type_by_id(type_id)
        if entity is None:
            raise AppException(ErrorCode.NOT_FOUND, "字典类型不存在")
        for field in ("name", "status", "remark"):
            value = getattr(payload, field)
            if value is not None:
                setattr(entity, field, value)
        await self._db.commit()
        await cache_delete(DICT_CACHE_ALL)
        await cache_delete_prefix(f"{DICT_CACHE_PREFIX}{entity.code}")
        return entity

    async def delete_type(self, type_id: int) -> None:
        """删除字典类型（软删，同时软删其字典项）。"""
        entity = await self._repo.get_type_by_id(type_id)
        if entity is None:
            raise AppException(ErrorCode.NOT_FOUND, "字典类型不存在")
        entity.is_deleted = True
        for item in await self._repo.list_items(entity.code):
            item.is_deleted = True
        await self._db.commit()
        await cache_delete(DICT_CACHE_ALL)
        await cache_delete_prefix(f"{DICT_CACHE_PREFIX}{entity.code}")

    async def create_item(self, type_code: str, payload: DictItemCreate) -> SysDictItem:
        """新增字典项。"""
        if await self._repo.get_type_by_code(type_code) is None:
            raise AppException(ErrorCode.NOT_FOUND, "字典类型不存在")
        entity = SysDictItem(
            type_code=type_code,
            label=payload.label,
            value=payload.value,
            sort=payload.sort,
            status=payload.status,
            remark=payload.remark,
        )
        self._db.add(entity)
        try:
            await self._db.commit()
        except IntegrityError as exc:
            await self._db.rollback()
            raise AppException(
                ErrorCode.CONFLICT, f"字典项值 {payload.value} 在该类型下已存在"
            ) from exc
        await cache_delete(DICT_CACHE_ALL)
        await cache_delete_prefix(f"{DICT_CACHE_PREFIX}{type_code}")
        return entity

    async def update_item(self, item_id: int, payload: DictItemUpdate) -> SysDictItem:
        """修改字典项（部分更新）。"""
        entity = await self._repo.get_item_by_id(item_id)
        if entity is None:
            raise AppException(ErrorCode.NOT_FOUND, "字典项不存在")
        for field in ("label", "value", "sort", "status", "remark"):
            value = getattr(payload, field)
            if value is not None:
                setattr(entity, field, value)
        try:
            await self._db.commit()
        except IntegrityError as exc:
            await self._db.rollback()
            raise AppException(
                ErrorCode.CONFLICT, f"字典项值 {entity.value} 在该类型下已存在"
            ) from exc
        await cache_delete(DICT_CACHE_ALL)
        await cache_delete_prefix(f"{DICT_CACHE_PREFIX}{entity.type_code}")
        return entity

    async def delete_item(self, item_id: int) -> None:
        """删除字典项（软删）。"""
        entity = await self._repo.get_item_by_id(item_id)
        if entity is None:
            raise AppException(ErrorCode.NOT_FOUND, "字典项不存在")
        entity.is_deleted = True
        await self._db.commit()
        await cache_delete(DICT_CACHE_ALL)
        await cache_delete_prefix(f"{DICT_CACHE_PREFIX}{entity.type_code}")
