"""商品数据访问（商品、分类、单位）。"""

from decimal import Decimal

from sqlalchemy import Select, and_, func, or_, select, text
from sqlalchemy.engine import Row

from app.models.base_data import BdGoods, BdGoodsCategory, BdUnit
from app.models.inventory import InvStock
from app.repositories.base import BaseRepository
from app.schemas.goods import GoodsQuery


class GoodsRepository(BaseRepository[BdGoods]):
    """商品仓储。"""

    model = BdGoods

    async def get_for_update(self, goods_id: int) -> BdGoods | None:
        stmt = (
            select(BdGoods)
            .where(BdGoods.id == goods_id, BdGoods.tenant_id == self.tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_code(self, code: str) -> BdGoods | None:
        stmt = select(BdGoods).where(BdGoods.code == code, BdGoods.tenant_id == self.tenant_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_barcode(self, barcode: str) -> BdGoods | None:
        stmt = select(BdGoods).where(
            BdGoods.barcode == barcode,
            BdGoods.is_deleted.is_(False),
            BdGoods.tenant_id == self.tenant_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def max_code(self, prefix: str) -> str | None:
        """查询指定前缀下的最大编码，用于生成下一个编码。"""
        stmt = select(BdGoods.code).where(
            BdGoods.code.like(f"{prefix}%"), BdGoods.tenant_id == self.tenant_id
        )
        codes = (await self.db.execute(stmt)).scalars().all()
        numeric_codes = [code for code in codes if code[len(prefix) :].isdigit()]
        return (
            max(numeric_codes, key=lambda code: int(code[len(prefix) :])) if numeric_codes else None
        )

    def build_query(self, query: GoodsQuery) -> Select:
        """构建商品列表查询（关联分类/单位名称）。"""
        stmt = (
            select(
                BdGoods,
                BdGoodsCategory.name.label("category_name"),
                BdUnit.name.label("unit_name"),
            )
            .outerjoin(
                BdGoodsCategory,
                and_(
                    BdGoods.category_id == BdGoodsCategory.id,
                    BdGoodsCategory.tenant_id == self.tenant_id,
                    BdGoodsCategory.is_deleted.is_(False),
                ),
            )
            .outerjoin(
                BdUnit,
                and_(
                    BdGoods.unit_id == BdUnit.id,
                    BdUnit.tenant_id == self.tenant_id,
                    BdUnit.is_deleted.is_(False),
                ),
            )
            .where(BdGoods.is_deleted.is_(False), BdGoods.tenant_id == self.tenant_id)
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

    async def total_stock_qty(self, goods_id: int) -> Decimal:
        stmt = select(func.coalesce(func.sum(InvStock.qty), 0)).where(
            InvStock.goods_id == goods_id,
            InvStock.tenant_id == self.tenant_id,
            InvStock.is_deleted.is_(False),
        )
        return (await self.db.execute(stmt)).scalar_one()

    async def has_nonzero_stock(self, goods_id: int) -> bool:
        stmt = (
            select(InvStock.id)
            .where(
                InvStock.goods_id == goods_id,
                InvStock.tenant_id == self.tenant_id,
                InvStock.is_deleted.is_(False),
                or_(InvStock.qty != 0, InvStock.locked_qty != 0),
            )
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none() is not None

    async def stock_quantities(self, goods_id: int) -> list[Row]:
        """调用方已锁商品或其单位；只读检查不引入反向锁等待。"""
        stmt = select(InvStock.qty, InvStock.locked_qty, InvStock.available_qty).where(
            InvStock.goods_id == goods_id,
            InvStock.tenant_id == self.tenant_id,
            InvStock.is_deleted.is_(False),
        )
        return list((await self.db.execute(stmt)).all())

    async def list_by_unit(self, unit_id: int) -> list[BdGoods]:
        """单位行锁保护新增引用与数量更新，此处不得反向锁商品。"""
        stmt = (
            select(BdGoods)
            .where(
                BdGoods.unit_id == unit_id,
                BdGoods.tenant_id == self.tenant_id,
                BdGoods.is_deleted.is_(False),
            )
            .order_by(BdGoods.id)
            .execution_options(populate_existing=True)
        )
        return list((await self.db.execute(stmt)).scalars().all())


class GoodsCategoryRepository(BaseRepository[BdGoodsCategory]):
    """商品分类仓储。"""

    model = BdGoodsCategory

    async def get_for_update(self, category_id: int) -> BdGoodsCategory | None:
        stmt = (
            select(BdGoodsCategory)
            .where(
                BdGoodsCategory.id == category_id,
                BdGoodsCategory.tenant_id == self.tenant_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def lock_tree(self) -> None:
        """同租户分类写操作串行化，阻止并发互设父节点形成环。"""
        if self.db.bind.dialect.name == "postgresql":
            await self.db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"goods-category:{self.tenant_id}"},
            )

    async def list_categories(self, keyword: str | None) -> list[BdGoodsCategory]:
        stmt = select(BdGoodsCategory).where(
            BdGoodsCategory.is_deleted.is_(False),
            BdGoodsCategory.tenant_id == self.tenant_id,
        )
        if keyword:
            stmt = stmt.where(BdGoodsCategory.name.ilike(f"%{keyword}%"))
        stmt = stmt.order_by(BdGoodsCategory.sort, BdGoodsCategory.id)
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_by_name(self, name: str, parent_id: int | None) -> BdGoodsCategory | None:
        stmt = select(BdGoodsCategory).where(
            BdGoodsCategory.name == name,
            BdGoodsCategory.parent_id == parent_id,
            BdGoodsCategory.tenant_id == self.tenant_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def count_children(self, category_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(BdGoodsCategory)
            .where(
                BdGoodsCategory.parent_id == category_id,
                BdGoodsCategory.tenant_id == self.tenant_id,
                BdGoodsCategory.is_deleted.is_(False),
            )
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def count_goods(self, category_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(BdGoods)
            .where(
                BdGoods.category_id == category_id,
                BdGoods.is_deleted.is_(False),
                BdGoods.tenant_id == self.tenant_id,
            )
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def children_count_map(self) -> dict[int, int]:
        """一次性统计各分类的子分类数量，避免列表接口逐行查询（N+1）。"""
        stmt = (
            select(BdGoodsCategory.parent_id, func.count())
            .where(
                BdGoodsCategory.parent_id.is_not(None),
                BdGoodsCategory.tenant_id == self.tenant_id,
                BdGoodsCategory.is_deleted.is_(False),
            )
            .group_by(BdGoodsCategory.parent_id)
        )
        return {
            int(parent_id): int(count) for parent_id, count in (await self.db.execute(stmt)).all()
        }

    async def goods_count_map(self) -> dict[int, int]:
        """一次性统计各分类关联的商品数量，供前端禁用删除按钮。"""
        stmt = (
            select(BdGoods.category_id, func.count())
            .where(
                BdGoods.category_id.is_not(None),
                BdGoods.tenant_id == self.tenant_id,
                BdGoods.is_deleted.is_(False),
            )
            .group_by(BdGoods.category_id)
        )
        return {
            int(category_id): int(count)
            for category_id, count in (await self.db.execute(stmt)).all()
        }


class UnitRepository(BaseRepository[BdUnit]):
    """计量单位仓储。"""

    model = BdUnit

    async def get_for_update(self, unit_id: int) -> BdUnit | None:
        stmt = (
            select(BdUnit)
            .where(
                BdUnit.id == unit_id,
                BdUnit.tenant_id == self.tenant_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_units(self, keyword: str | None) -> list[BdUnit]:
        stmt = select(BdUnit).where(
            BdUnit.is_deleted.is_(False), BdUnit.tenant_id == self.tenant_id
        )
        if keyword:
            stmt = stmt.where(BdUnit.name.ilike(f"%{keyword}%"))
        return list((await self.db.execute(stmt.order_by(BdUnit.id))).scalars().all())

    async def get_by_name(self, name: str) -> BdUnit | None:
        stmt = select(BdUnit).where(BdUnit.name == name, BdUnit.tenant_id == self.tenant_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def count_goods(self, unit_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(BdGoods)
            .where(
                BdGoods.unit_id == unit_id,
                BdGoods.is_deleted.is_(False),
                BdGoods.tenant_id == self.tenant_id,
            )
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def goods_count_map(self) -> dict[int, int]:
        """一次性统计各单位被商品引用的数量。"""
        stmt = (
            select(BdGoods.unit_id, func.count())
            .where(
                BdGoods.unit_id.is_not(None),
                BdGoods.tenant_id == self.tenant_id,
                BdGoods.is_deleted.is_(False),
            )
            .group_by(BdGoods.unit_id)
        )
        return {int(unit_id): int(count) for unit_id, count in (await self.db.execute(stmt)).all()}
