"""商品数据访问（商品、分类、单位）。"""

from sqlalchemy import Select, func, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base_data import BdGoods, BdGoodsCategory, BdUnit
from app.repositories.base import BaseRepository
from app.schemas.goods import GoodsQuery


class GoodsRepository(BaseRepository[BdGoods]):
    """商品仓储。"""

    model = BdGoods

    async def get_by_code(self, code: str) -> BdGoods | None:
        stmt = select(BdGoods).where(BdGoods.code == code, BdGoods.is_deleted.is_(False))
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_barcode(self, barcode: str) -> BdGoods | None:
        stmt = select(BdGoods).where(BdGoods.barcode == barcode, BdGoods.is_deleted.is_(False))
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def max_code(self, prefix: str) -> str | None:
        """查询指定前缀下的最大编码，用于生成下一个编码。"""
        stmt = select(BdGoods.code).where(BdGoods.code.like(f"{prefix}%"))
        codes = (await self.db.execute(stmt)).scalars().all()
        return max(codes) if codes else None

    def build_query(self, query: GoodsQuery) -> Select:
        """构建商品列表查询（关联分类/单位名称）。"""
        stmt = (
            select(
                BdGoods,
                BdGoodsCategory.name.label("category_name"),
                BdUnit.name.label("unit_name"),
            )
            .outerjoin(BdGoodsCategory, BdGoods.category_id == BdGoodsCategory.id)
            .outerjoin(BdUnit, BdGoods.unit_id == BdUnit.id)
            .where(BdGoods.is_deleted.is_(False))
        )
        if query.keyword:
            like = f"%{query.keyword}%"
            stmt = stmt.where(
                or_(
                    BdGoods.code.ilike(like),
                    BdGoods.name.ilike(like),
                    BdGoods.barcode.ilike(like),
                )
            )
        if query.category_id is not None:
            stmt = stmt.where(BdGoods.category_id == query.category_id)
        if query.status is not None:
            stmt = stmt.where(BdGoods.status == query.status)
        return stmt.order_by(BdGoods.id.desc())

    async def list_goods(
        self, query: GoodsQuery, limit: int | None = None
    ) -> tuple[list[Row], int]:
        """查询商品列表，返回 (商品行+分类名+单位名, 总数)。"""
        stmt = self.build_query(query)
        if limit is not None:
            rows = list((await self.db.execute(stmt.limit(limit))).all())
            return rows, len(rows)
        return await self.paginate_rows(stmt, query.page, query.page_size)


class GoodsCategoryRepository(BaseRepository[BdGoodsCategory]):
    """商品分类仓储。"""

    model = BdGoodsCategory

    async def list_categories(self, keyword: str | None) -> list[BdGoodsCategory]:
        stmt = select(BdGoodsCategory).where(BdGoodsCategory.is_deleted.is_(False))
        if keyword:
            stmt = stmt.where(BdGoodsCategory.name.ilike(f"%{keyword}%"))
        return list((await self.db.execute(stmt.order_by(BdGoodsCategory.sort, BdGoodsCategory.id))).scalars().all())

    async def get_by_name(self, name: str, parent_id: int | None) -> BdGoodsCategory | None:
        stmt = select(BdGoodsCategory).where(
            BdGoodsCategory.name == name,
            BdGoodsCategory.parent_id == parent_id,
            BdGoodsCategory.is_deleted.is_(False),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def count_children(self, category_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(BdGoodsCategory)
            .where(
                BdGoodsCategory.parent_id == category_id,
                BdGoodsCategory.is_deleted.is_(False),
            )
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def count_goods(self, category_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(BdGoods)
            .where(BdGoods.category_id == category_id, BdGoods.is_deleted.is_(False))
        )
        return int((await self.db.execute(stmt)).scalar_one())


class UnitRepository(BaseRepository[BdUnit]):
    """计量单位仓储。"""

    model = BdUnit

    async def list_units(self, keyword: str | None) -> list[BdUnit]:
        stmt = select(BdUnit).where(BdUnit.is_deleted.is_(False))
        if keyword:
            stmt = stmt.where(BdUnit.name.ilike(f"%{keyword}%"))
        return list((await self.db.execute(stmt.order_by(BdUnit.id))).scalars().all())

    async def get_by_name(self, name: str) -> BdUnit | None:
        stmt = select(BdUnit).where(BdUnit.name == name, BdUnit.is_deleted.is_(False))
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def count_goods(self, unit_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(BdGoods)
            .where(BdGoods.unit_id == unit_id, BdGoods.is_deleted.is_(False))
        )
        return int((await self.db.execute(stmt)).scalar_one())
