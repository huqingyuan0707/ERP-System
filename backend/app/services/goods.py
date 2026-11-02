"""商品、分类、单位规则；文件导入复用 prepare_create，不在此处理文件。"""

from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ErrorCode
from app.models.base_data import BdGoods, BdGoodsCategory, BdUnit
from app.repositories.goods import GoodsCategoryRepository, GoodsRepository, UnitRepository
from app.schemas.common import PageResult
from app.schemas.goods import (
    GoodsCategoryCreate,
    GoodsCategoryOut,
    GoodsCategoryQuery,
    GoodsCategoryUpdate,
    GoodsCreate,
    GoodsOut,
    GoodsQuery,
    GoodsUpdate,
    UnitCreate,
    UnitOut,
    UnitQuery,
    UnitUpdate,
)
from app.services.audit import write_oper_log
from app.utils.code_gen import format_code, next_seq_from_code
from app.utils.numbers import DEFAULT_QUANTITY_PRECISION, validate_quantity
from app.utils.pagination import paginate_result


GOODS_CODE_PREFIX = "GD"


def _to_goods_out(goods: BdGoods, category_name: str | None, unit_name: str | None) -> GoodsOut:
    data = {column.name: getattr(goods, column.name) for column in goods.__table__.columns}
    data.update(category_name=category_name, unit_name=unit_name)
    return GoodsOut.model_validate(data)


async def _finish(db, entity, *, tenant_id, operator_id, action, commit):
    try:
        await db.flush()
        await db.refresh(entity)
        if operator_id is not None:
            await write_oper_log(
                db,
                tenant_id=tenant_id,
                operator_id=operator_id,
                action=action,
                params={"id": entity.id},
            )
        if commit:
            await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise AppException(ErrorCode.CONFLICT, "编码、名称或条码已被占用，请刷新后重试") from exc


class GoodsService:
    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.goods = GoodsRepository(db, tenant_id=tenant_id)
        self.categories = GoodsCategoryRepository(db, tenant_id=tenant_id)
        self.units = UnitRepository(db, tenant_id=tenant_id)

    async def list_goods(self, query: GoodsQuery) -> PageResult[GoodsOut]:
        rows, total = await self.goods.list_goods(query)
        return paginate_result(
            [_to_goods_out(row[0], row[1], row[2]) for row in rows],
            total,
            query.page,
            query.page_size,
        )

    async def get_goods(self, goods_id: int) -> GoodsOut:
        goods = await self._get_active_goods(goods_id)
        return _to_goods_out(goods, *await self._relation_names(goods))

    async def prepare_create(self, payload: GoodsCreate, operator_id: int) -> BdGoods:
        """校验并准备尚未加入 Session 的实体，供手工新增及 Excel 导入复用。"""
        code = payload.code or await self._next_code()
        precision = await self._validate_relations(payload.category_id, payload.unit_id)
        await self._validate_unique(code, payload.barcode)
        self._validate_stock_range(payload.stock_lower, payload.stock_upper, precision)
        return BdGoods(
            tenant_id=self.tenant_id,
            code=code,
            **payload.model_dump(exclude={"code"}),
            created_by=operator_id,
            updated_by=operator_id,
        )

    async def create_goods(
        self,
        payload: GoodsCreate,
        operator_id: int | None = None,
        *,
        commit: bool = True,
    ) -> GoodsOut:
        goods = await self.prepare_create(payload, operator_id)
        self.db.add(goods)
        await _finish(
            self.db,
            goods,
            tenant_id=self.tenant_id,
            operator_id=operator_id,
            action="新增商品",
            commit=commit,
        )
        return _to_goods_out(goods, *await self._relation_names(goods))

    async def update_goods(
        self,
        goods_id: int,
        payload: GoodsUpdate,
        operator_id: int | None = None,
        *,
        commit: bool = True,
    ) -> GoodsOut:
        goods = await self._get_active_goods(goods_id, for_update=True)
        data = payload.model_dump(exclude_unset=True)
        category_id = data.get("category_id", goods.category_id)
        unit_id = data.get("unit_id", goods.unit_id)
        precision = await self._validate_relations(category_id, unit_id)
        await self._validate_unique(goods.code, data.get("barcode", goods.barcode), goods.id)
        self._validate_stock_range(
            data.get("stock_lower", goods.stock_lower),
            data.get("stock_upper", goods.stock_upper),
            precision,
        )
        if unit_id != goods.unit_id:
            await self.validate_stock_precision(goods_id, precision)
        for field, value in data.items():
            setattr(goods, field, value)
        goods.updated_by = operator_id
        await _finish(
            self.db,
            goods,
            tenant_id=self.tenant_id,
            operator_id=operator_id,
            action="修改商品",
            commit=commit,
        )
        return _to_goods_out(goods, *await self._relation_names(goods))

    async def delete_goods(
        self,
        goods_id: int,
        operator_id: int | None = None,
        *,
        commit: bool = True,
    ) -> None:
        goods = await self._get_active_goods(goods_id, for_update=True)
        if await self.goods.has_nonzero_stock(goods_id):
            raise AppException(ErrorCode.CONFLICT, "该商品存在库存或锁定数量，无法删除")
        goods.is_deleted = True
        goods.updated_by = operator_id
        await _finish(
            self.db,
            goods,
            tenant_id=self.tenant_id,
            operator_id=operator_id,
            action="删除商品",
            commit=commit,
        )

    async def validate_stock_precision(self, goods_id: int, precision: int) -> None:
        for row in await self.goods.stock_quantities(goods_id):
            for value in row:
                try:
                    validate_quantity(value, precision)
                except AppException as exc:
                    raise AppException(
                        ErrorCode.CONFLICT, "现有库存数量与目标单位精度不兼容"
                    ) from exc

    async def _relation_names(self, goods: BdGoods) -> tuple[str | None, str | None]:
        category = await self.categories.get(goods.category_id) if goods.category_id else None
        unit = await self.units.get(goods.unit_id) if goods.unit_id else None
        return (
            category.name if category and not category.is_deleted else None,
            unit.name if unit and not unit.is_deleted else None,
        )

    async def _validate_relations(self, category_id: int | None, unit_id: int | None) -> int:
        if category_id is not None:
            category = await self.categories.get_for_update(category_id)
            if category is None or category.is_deleted:
                raise AppException(ErrorCode.VALIDATION_ERROR, "商品分类不存在")
        if unit_id is not None:
            unit = await self.units.get_for_update(unit_id)
            if unit is None or unit.is_deleted:
                raise AppException(ErrorCode.VALIDATION_ERROR, "计量单位不存在")
            return unit.precision
        return DEFAULT_QUANTITY_PRECISION

    async def _validate_unique(
        self,
        code: str,
        barcode: str | None,
        exclude_id: int | None = None,
    ) -> None:
        existing = await self.goods.get_by_code(code)
        if existing is not None and existing.id != exclude_id:
            raise AppException(ErrorCode.CONFLICT, f"商品编码已存在: {code}")
        if barcode:
            existing = await self.goods.get_by_barcode(barcode)
            if existing is not None and existing.id != exclude_id:
                raise AppException(ErrorCode.CONFLICT, f"商品条码已存在: {barcode}")

    @staticmethod
    def _validate_stock_range(lower: Decimal, upper: Decimal, precision: int) -> None:
        validate_quantity(lower, precision)
        validate_quantity(upper, precision)
        if lower > upper:
            raise AppException(ErrorCode.VALIDATION_ERROR, "库存下限不能大于库存上限")

    async def _next_code(self) -> str:
        max_code = await self.goods.max_code(GOODS_CODE_PREFIX)
        return format_code(GOODS_CODE_PREFIX, next_seq_from_code(max_code, GOODS_CODE_PREFIX))

    async def _get_active_goods(self, goods_id: int, for_update: bool = False) -> BdGoods:
        goods = await (
            self.goods.get_for_update(goods_id) if for_update else self.goods.get(goods_id)
        )
        if goods is None or goods.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "商品不存在")
        return goods


class GoodsCategoryService:
    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.categories = GoodsCategoryRepository(db, tenant_id=tenant_id)

    async def list_categories(self, query: GoodsCategoryQuery) -> list[GoodsCategoryOut]:
        rows = await self.categories.list_categories(query.keyword)
        children_map = await self.categories.children_count_map()
        goods_map = await self.categories.goods_count_map()
        return [
            GoodsCategoryOut.model_validate(row).model_copy(
                update={
                    "children_count": children_map.get(row.id, 0),
                    "goods_count": goods_map.get(row.id, 0),
                }
            )
            for row in rows
        ]

    async def get_category(self, category_id: int) -> GoodsCategoryOut:
        category = await self._get_active(category_id)
        return GoodsCategoryOut.model_validate(category).model_copy(
            update={
                "children_count": await self.categories.count_children(category_id),
                "goods_count": await self.categories.count_goods(category_id),
            }
        )

    async def create_category(
        self,
        payload: GoodsCategoryCreate,
        operator_id: int | None = None,
        *,
        commit: bool = True,
    ) -> GoodsCategoryOut:
        await self.categories.lock_tree()
        if await self.categories.get_by_name(payload.name, payload.parent_id):
            raise AppException(ErrorCode.CONFLICT, "同级下分类名称已存在")
        if payload.parent_id is not None:
            await self._ensure_parent(payload.parent_id, None)
        category = BdGoodsCategory(
            tenant_id=self.tenant_id,
            **payload.model_dump(),
            created_by=operator_id,
            updated_by=operator_id,
        )
        self.db.add(category)
        await _finish(
            self.db,
            category,
            tenant_id=self.tenant_id,
            operator_id=operator_id,
            action="新增商品分类",
            commit=commit,
        )
        return GoodsCategoryOut.model_validate(category)

    async def update_category(
        self,
        category_id: int,
        payload: GoodsCategoryUpdate,
        operator_id: int | None = None,
        *,
        commit: bool = True,
    ) -> GoodsCategoryOut:
        await self.categories.lock_tree()
        category = await self._get_active(category_id, for_update=True)
        data = payload.model_dump(exclude_unset=True)
        parent_id = data.get("parent_id", category.parent_id)
        name = data.get("name", category.name)
        duplicate = await self.categories.get_by_name(name, parent_id)
        if duplicate is not None and duplicate.id != category_id:
            raise AppException(ErrorCode.CONFLICT, "同级下分类名称已存在")
        if parent_id is not None:
            await self._ensure_parent(parent_id, category_id)
        for field, value in data.items():
            setattr(category, field, value)
        category.updated_by = operator_id
        await _finish(
            self.db,
            category,
            tenant_id=self.tenant_id,
            operator_id=operator_id,
            action="修改商品分类",
            commit=commit,
        )
        return await self.get_category(category_id)

    async def delete_category(
        self,
        category_id: int,
        operator_id: int | None = None,
        *,
        commit: bool = True,
    ) -> None:
        await self.categories.lock_tree()
        category = await self._get_active(category_id, for_update=True)
        if await self.categories.count_children(category_id):
            raise AppException(ErrorCode.CONFLICT, "该分类下存在子分类，无法删除")
        if await self.categories.count_goods(category_id):
            raise AppException(ErrorCode.CONFLICT, "该分类下存在商品，无法删除")
        category.is_deleted = True
        category.updated_by = operator_id
        await _finish(
            self.db,
            category,
            tenant_id=self.tenant_id,
            operator_id=operator_id,
            action="删除商品分类",
            commit=commit,
        )

    async def _ensure_parent(self, parent_id: int, self_id: int | None) -> None:
        visited: set[int] = set()
        current_id = parent_id
        while current_id is not None:
            if current_id == self_id or current_id in visited:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR, "上级分类不能是自身、其下级或循环分类"
                )
            visited.add(current_id)
            parent = await self.categories.get_for_update(current_id)
            if parent is None or parent.is_deleted:
                raise AppException(ErrorCode.VALIDATION_ERROR, "上级分类不存在")
            current_id = parent.parent_id

    async def _get_active(self, category_id: int, for_update: bool = False) -> BdGoodsCategory:
        category = await (
            self.categories.get_for_update(category_id)
            if for_update
            else self.categories.get(category_id)
        )
        if category is None or category.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "商品分类不存在")
        return category


class UnitService:
    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.units = UnitRepository(db, tenant_id=tenant_id)

    async def list_units(self, query: UnitQuery) -> list[UnitOut]:
        rows = await self.units.list_units(query.keyword)
        goods_map = await self.units.goods_count_map()
        return [
            UnitOut.model_validate(row).model_copy(
                update={"goods_count": goods_map.get(row.id, 0)},
            )
            for row in rows
        ]

    async def get_unit(self, unit_id: int) -> UnitOut:
        unit = await self._get_active(unit_id)
        return UnitOut.model_validate(unit).model_copy(
            update={"goods_count": await self.units.count_goods(unit_id)},
        )

    async def create_unit(
        self,
        payload: UnitCreate,
        operator_id: int | None = None,
        *,
        commit: bool = True,
    ) -> UnitOut:
        if await self.units.get_by_name(payload.name):
            raise AppException(ErrorCode.CONFLICT, "单位名称已存在")
        unit = BdUnit(
            tenant_id=self.tenant_id,
            **payload.model_dump(),
            created_by=operator_id,
            updated_by=operator_id,
        )
        self.db.add(unit)
        await _finish(
            self.db,
            unit,
            tenant_id=self.tenant_id,
            operator_id=operator_id,
            action="新增计量单位",
            commit=commit,
        )
        return UnitOut.model_validate(unit)

    async def update_unit(
        self,
        unit_id: int,
        payload: UnitUpdate,
        operator_id: int | None = None,
        *,
        commit: bool = True,
    ) -> UnitOut:
        unit = await self._get_active(unit_id, for_update=True)
        data = payload.model_dump(exclude_unset=True)
        duplicate = await self.units.get_by_name(data.get("name", unit.name))
        if duplicate is not None and duplicate.id != unit_id:
            raise AppException(ErrorCode.CONFLICT, "单位名称已存在")
        precision = data.get("precision", unit.precision)
        if precision < unit.precision:
            service = GoodsService(self.db, self.tenant_id)
            for goods in await service.goods.list_by_unit(unit_id):
                try:
                    validate_quantity(goods.stock_lower, precision)
                    validate_quantity(goods.stock_upper, precision)
                except AppException as exc:
                    raise AppException(
                        ErrorCode.CONFLICT, "商品库存上下限与目标单位精度不兼容"
                    ) from exc
                await service.validate_stock_precision(goods.id, precision)
        for field, value in data.items():
            setattr(unit, field, value)
        unit.updated_by = operator_id
        await _finish(
            self.db,
            unit,
            tenant_id=self.tenant_id,
            operator_id=operator_id,
            action="修改计量单位",
            commit=commit,
        )
        return await self.get_unit(unit_id)

    async def delete_unit(
        self,
        unit_id: int,
        operator_id: int | None = None,
        *,
        commit: bool = True,
    ) -> None:
        unit = await self._get_active(unit_id, for_update=True)
        if await self.units.count_goods(unit_id):
            raise AppException(ErrorCode.CONFLICT, "该单位下存在商品，无法删除")
        unit.is_deleted = True
        unit.updated_by = operator_id
        await _finish(
            self.db,
            unit,
            tenant_id=self.tenant_id,
            operator_id=operator_id,
            action="删除计量单位",
            commit=commit,
        )

    async def _get_active(self, unit_id: int, for_update: bool = False) -> BdUnit:
        unit = await (self.units.get_for_update(unit_id) if for_update else self.units.get(unit_id))
        if unit is None or unit.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "计量单位不存在")
        return unit
