"""字典类型/字典项数据访问。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sys import SysDictItem, SysDictType


class DictRepository:
    """字典数据访问（不含业务规则）。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_types(self) -> list[SysDictType]:
        """全部未删除的字典类型，按 code 排序。"""
        stmt = (
            select(SysDictType)
            .where(SysDictType.is_deleted.is_(False))
            .order_by(SysDictType.code)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_type_by_id(self, type_id: int) -> SysDictType | None:
        """按 id 查询字典类型（未删除）。"""
        return await self._db.scalar(
            select(SysDictType).where(
                SysDictType.id == type_id, SysDictType.is_deleted.is_(False)
            )
        )

    async def get_type_by_code(self, code: str) -> SysDictType | None:
        """按 code 查询字典类型（未删除）。"""
        return await self._db.scalar(
            select(SysDictType).where(SysDictType.code == code, SysDictType.is_deleted.is_(False))
        )

    async def list_items(self, type_code: str) -> list[SysDictItem]:
        """某类型全部字典项（含停用），按 sort,id 排序。"""
        stmt = (
            select(SysDictItem)
            .where(SysDictItem.type_code == type_code, SysDictItem.is_deleted.is_(False))
            .order_by(SysDictItem.sort, SysDictItem.id)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def list_all_enabled_items(self) -> list[SysDictItem]:
        """全部启用字典项，按 type_code,sort,id 排序。"""
        stmt = (
            select(SysDictItem)
            .where(SysDictItem.is_deleted.is_(False), SysDictItem.status == 1)
            .order_by(SysDictItem.type_code, SysDictItem.sort, SysDictItem.id)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_item_by_id(self, item_id: int) -> SysDictItem | None:
        """按 id 查询字典项（未删除）。"""
        return await self._db.scalar(
            select(SysDictItem).where(SysDictItem.id == item_id, SysDictItem.is_deleted.is_(False))
        )
