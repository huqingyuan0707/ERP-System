"""商品业务逻辑（商品、分类、单位 CRUD + 导入导出）。"""

from decimal import Decimal

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
    GoodsImportResult,
    GoodsImportRow,
    GoodsOut,
    GoodsQuery,
    GoodsUpdate,
    UnitCreate,
    UnitOut,
    UnitQuery,
    UnitUpdate,
)
from app.utils.code_gen import format_code, next_seq_from_code
from app.utils.importer import IMPORT_HEADERS, build_import_template, parse_import_rows, to_float, to_str
from app.utils.pagination import paginate_result


GOODS_CODE_PREFIX = "GD"
EXPORT_LIMIT = 10000
EXPORT_HEADERS = [
    "编码",
    "名称",
    "规格",
    "分类",
    "单位",
    "条码",
    "成本价",
    "售价",
    "库存下限",
    "库存上限",
    "状态",
]


def _to_goods_out(
    goods: BdGoods, category_name: str | None, unit_name: str | None
) -> GoodsOut:
    """将 ORM 行（含关联名称）转换为出参。"""
    data = {column.name: getattr(goods, column.name) for column in goods.__table__.columns}
    for key in ("cost_price", "sale_price", "stock_lower", "stock_upper"):
        value = data.get(key)
        if isinstance(value, Decimal):
            data[key] = float(value)
    data["category_name"] = category_name
    data["unit_name"] = unit_name
    return GoodsOut.model_validate(data)


class GoodsService:
    """商品 CRUD、自动编码、导入与导出。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.goods = GoodsRepository(db)
        self.categories = GoodsCategoryRepository(db)
        self.units = UnitRepository(db)

    async def list_goods(self, query: GoodsQuery) -> PageResult[GoodsOut]:
        rows, total = await self.goods.list_goods(query)
        items = [_to_goods_out(row[0], row[1], row[2]) for row in rows]
        return paginate_result(items, total, query.page, query.page_size)

    async def get_goods(self, goods_id: int) -> GoodsOut:
        goods = await self._get_active_goods(goods_id)
        category_name, unit_name = await self._relation_names(goods)
        return _to_goods_out(goods, category_name, unit_name)

    async def create_goods(self, payload: GoodsCreate) -> GoodsOut:
        code = (payload.code or "").strip() or await self._next_code()
        await self._validate_relations(payload.category_id, payload.unit_id)
        await self._validate_unique(code=code, barcode=payload.barcode)
        self._validate_stock_range(payload.stock_lower, payload.stock_upper)
        goods = BdGoods(
            code=code,
            name=payload.name.strip(),
            category_id=payload.category_id,
            spec=payload.spec,
            unit_id=payload.unit_id,
            barcode=(payload.barcode or "").strip() or None,
            cost_price=payload.cost_price,
            sale_price=payload.sale_price,
            stock_lower=payload.stock_lower,
            stock_upper=payload.stock_upper,
            status=payload.status,
            remark=payload.remark,
        )
        await self.goods.add(goods)
        await self.db.commit()
        category_name, unit_name = await self._relation_names(goods)
        return _to_goods_out(goods, category_name, unit_name)

    async def update_goods(self, goods_id: int, payload: GoodsUpdate) -> GoodsOut:
        goods = await self._get_active_goods(goods_id)
        data = payload.model_dump(exclude_unset=True)
        category_id = data.get("category_id", goods.category_id)
        unit_id = data.get("unit_id", goods.unit_id)
        barcode = data.get("barcode", goods.barcode)
        stock_lower = data.get("stock_lower")
        stock_upper = data.get("stock_upper")
        await self._validate_relations(category_id, unit_id)
        await self._validate_unique(
            code=goods.code, barcode=barcode, exclude_id=goods.id
        )
        self._validate_stock_range(
            float(stock_lower) if stock_lower is not None else float(goods.stock_lower),
            float(stock_upper) if stock_upper is not None else float(goods.stock_upper),
        )
        for field, value in data.items():
            if field == "name" and isinstance(value, str):
                value = value.strip()
            if field == "barcode":
                value = (value or "").strip() or None
            setattr(goods, field, value)
        await self.db.commit()
        await self.db.refresh(goods)
        category_name, unit_name = await self._relation_names(goods)
        return _to_goods_out(goods, category_name, unit_name)

    async def delete_goods(self, goods_id: int) -> None:
        goods = await self._get_active_goods(goods_id)
        goods.is_deleted = True
        await self.db.commit()

    async def export_goods(self, query: GoodsQuery) -> tuple[list[str], list[list[object]]]:
        rows, _ = await self.goods.list_goods(query, limit=EXPORT_LIMIT)
        data = [
            [
                goods.code,
                goods.name,
                goods.spec or "",
                category_name or "",
                unit_name or "",
                goods.barcode or "",
                float(goods.cost_price),
                float(goods.sale_price),
                float(goods.stock_lower),
                float(goods.stock_upper),
                goods.status,
            ]
            for goods, category_name, unit_name in rows
        ]
        return EXPORT_HEADERS, data

    def build_import_template(self) -> bytes:
        """生成导入模板文件字节。"""
        return build_import_template()

    async def import_goods(self, content: bytes) -> GoodsImportResult:
        """解析上传的 Excel 并批量导入商品。

        分类/单位按名称自动匹配，不存在则自动创建；失败行不影响成功行。
        """
        parsed = parse_import_rows(content)
        result = GoodsImportResult(total=len(parsed))
        seen_codes: set[str] = set()
        seen_barcodes: set[str] = set()

        for row_no, item in parsed:
            name = to_str(item.get("商品名称"))
            code = to_str(item.get("商品编码"))
            barcode = to_str(item.get("条码"))
            if not name:
                result.rows.append(
                    GoodsImportRow(row=row_no, success=False, message="商品名称不能为空")
                )
                continue
            if code and code in seen_codes:
                result.rows.append(
                    GoodsImportRow(row=row_no, code=code, name=name, success=False, message="文件内编码重复")
                )
                continue
            if code and await self.goods.get_by_code(code):
                result.rows.append(
                    GoodsImportRow(row=row_no, code=code, name=name, success=False, message=f"编码已存在: {code}")
                )
                continue
            if barcode and barcode in seen_barcodes:
                result.rows.append(
                    GoodsImportRow(row=row_no, code=code, name=name, success=False, message="文件内条码重复")
                )
                continue
            if barcode and await self.goods.get_by_barcode(barcode):
                result.rows.append(
                    GoodsImportRow(row=row_no, code=code, name=name, success=False, message=f"条码已存在: {barcode}")
                )
                continue

            category = await self._resolve_category(to_str(item.get("分类名称")))
            unit = await self._resolve_unit(to_str(item.get("单位名称")))
            stock_lower = to_float(item.get("库存下限"))
            stock_upper = to_float(item.get("库存上限"))
            if stock_lower > stock_upper:
                result.rows.append(
                    GoodsImportRow(row=row_no, code=code, name=name, success=False, message="库存下限不能大于上限")
                )
                continue

            final_code = code or await self._next_code()
            goods = BdGoods(
                code=final_code,
                name=name,
                category_id=category.id if category else None,
                spec=to_str(item.get("规格")) or None,
                unit_id=unit.id if unit else None,
                barcode=barcode or None,
                cost_price=to_float(item.get("成本价")),
                sale_price=to_float(item.get("售价")),
                stock_lower=stock_lower,
                stock_upper=stock_upper,
                status=1,
                remark=to_str(item.get("备注")) or None,
            )
            self.db.add(goods)
            await self.db.flush()
            seen_codes.add(final_code)
            if barcode:
                seen_barcodes.add(barcode)
            result.success += 1

        await self.db.commit()
        result.failed = result.total - result.success
        return result

    async def _resolve_category(self, name: str) -> BdGoodsCategory | None:
        """按名称解析分类，不存在则创建。"""
        if not name:
            return None
        category = await self.categories.get_by_name(name, None)
        if category is None:
            category = await self.categories.add(BdGoodsCategory(name=name, sort=0))
        return category

    async def _resolve_unit(self, name: str) -> BdUnit | None:
        """按名称解析单位，不存在则创建。"""
        if not name:
            return None
        unit = await self.units.get_by_name(name)
        if unit is None:
            unit = await self.units.add(BdUnit(name=name, precision=0))
        return unit

    async def _relation_names(self, goods: BdGoods) -> tuple[str | None, str | None]:
        category_name = None
        unit_name = None
        if goods.category_id is not None:
            category = await self.categories.get(goods.category_id)
            category_name = category.name if category else None
        if goods.unit_id is not None:
            unit = await self.units.get(goods.unit_id)
            unit_name = unit.name if unit else None
        return category_name, unit_name

    async def _validate_relations(
        self, category_id: int | None, unit_id: int | None
    ) -> None:
        if category_id is not None:
            category = await self.categories.get(category_id)
            if category is None or category.is_deleted:
                raise AppException(ErrorCode.BAD_REQUEST, "商品分类不存在")
        if unit_id is not None:
            unit = await self.units.get(unit_id)
            if unit is None or unit.is_deleted:
                raise AppException(ErrorCode.BAD_REQUEST, "计量单位不存在")

    async def _validate_unique(
        self, code: str, barcode: str | None, exclude_id: int | None = None
    ) -> None:
        existing = await self.goods.get_by_code(code)
        if existing is not None and existing.id != exclude_id:
            raise AppException(ErrorCode.CONFLICT, f"商品编码已存在: {code}")
        if barcode:
            existing_barcode = await self.goods.get_by_barcode(barcode)
            if existing_barcode is not None and existing_barcode.id != exclude_id:
                raise AppException(ErrorCode.CONFLICT, f"商品条码已存在: {barcode}")

    def _validate_stock_range(self, stock_lower: float, stock_upper: float) -> None:
        if stock_lower > stock_upper:
            raise AppException(ErrorCode.BAD_REQUEST, "库存下限不能大于库存上限")

    async def _next_code(self) -> str:
        max_code = await self.goods.max_code(GOODS_CODE_PREFIX)
        return format_code(GOODS_CODE_PREFIX, next_seq_from_code(max_code, GOODS_CODE_PREFIX))

    async def _get_active_goods(self, goods_id: int) -> BdGoods:
        goods = await self.goods.get(goods_id)
        if goods is None or goods.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "商品不存在")
        return goods


class GoodsCategoryService:
    """商品分类 CRUD（删除前校验子分类与商品引用）。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.categories = GoodsCategoryRepository(db)

    async def list_categories(self, query: GoodsCategoryQuery) -> list[GoodsCategoryOut]:
        rows = await self.categories.list_categories(query.keyword)
        return [GoodsCategoryOut.model_validate(row) for row in rows]

    async def get_category(self, category_id: int) -> GoodsCategoryOut:
        category = await self._get_active(category_id)
        return GoodsCategoryOut.model_validate(category)

    async def create_category(self, payload: GoodsCategoryCreate) -> GoodsCategoryOut:
        name = payload.name.strip()
        if await self.categories.get_by_name(name, payload.parent_id):
            raise AppException(ErrorCode.CONFLICT, "同级下分类名称已存在")
        if payload.parent_id is not None:
            await self._ensure_parent(payload.parent_id, None)
        category = BdGoodsCategory(
            name=name,
            parent_id=payload.parent_id,
            sort=payload.sort,
            remark=payload.remark,
        )
        await self.categories.add(category)
        await self.db.commit()
        return GoodsCategoryOut.model_validate(category)

    async def update_category(
        self, category_id: int, payload: GoodsCategoryUpdate
    ) -> GoodsCategoryOut:
        category = await self._get_active(category_id)
        data = payload.model_dump(exclude_unset=True)
        parent_id = data.get("parent_id", category.parent_id)
        name = data.get("name", category.name)
        if isinstance(name, str):
            name = name.strip()
        duplicate = await self.categories.get_by_name(name, parent_id)
        if duplicate is not None and duplicate.id != category_id:
            raise AppException(ErrorCode.CONFLICT, "同级下分类名称已存在")
        if parent_id is not None:
            await self._ensure_parent(parent_id, category_id)
        for field, value in data.items():
            setattr(category, field, value)
        await self.db.commit()
        await self.db.refresh(category)
        return GoodsCategoryOut.model_validate(category)

    async def delete_category(self, category_id: int) -> None:
        category = await self._get_active(category_id)
        if await self.categories.count_children(category_id) > 0:
            raise AppException(ErrorCode.BAD_REQUEST, "该分类下存在子分类，无法删除")
        if await self.categories.count_goods(category_id) > 0:
            raise AppException(ErrorCode.BAD_REQUEST, "该分类下存在商品，无法删除")
        category.is_deleted = True
        await self.db.commit()

    async def _ensure_parent(self, parent_id: int, self_id: int | None) -> None:
        parent = await self.categories.get(parent_id)
        if parent is None or parent.is_deleted:
            raise AppException(ErrorCode.BAD_REQUEST, "上级分类不存在")
        if self_id is not None and parent.id == self_id:
            raise AppException(ErrorCode.BAD_REQUEST, "上级分类不能是自身")

    async def _get_active(self, category_id: int) -> BdGoodsCategory:
        category = await self.categories.get(category_id)
        if category is None or category.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "商品分类不存在")
        return category


class UnitService:
    """计量单位 CRUD（删除前校验商品引用）。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.units = UnitRepository(db)

    async def list_units(self, query: UnitQuery) -> list[UnitOut]:
        rows = await self.units.list_units(query.keyword)
        return [UnitOut.model_validate(row) for row in rows]

    async def get_unit(self, unit_id: int) -> UnitOut:
        unit = await self._get_active(unit_id)
        return UnitOut.model_validate(unit)

    async def create_unit(self, payload: UnitCreate) -> UnitOut:
        name = payload.name.strip()
        if await self.units.get_by_name(name):
            raise AppException(ErrorCode.CONFLICT, "单位名称已存在")
        unit = BdUnit(name=name, precision=payload.precision, remark=payload.remark)
        await self.units.add(unit)
        await self.db.commit()
        return UnitOut.model_validate(unit)

    async def update_unit(self, unit_id: int, payload: UnitUpdate) -> UnitOut:
        unit = await self._get_active(unit_id)
        data = payload.model_dump(exclude_unset=True)
        name = data.get("name", unit.name)
        if isinstance(name, str):
            name = name.strip()
        duplicate = await self.units.get_by_name(name)
        if duplicate is not None and duplicate.id != unit_id:
            raise AppException(ErrorCode.CONFLICT, "单位名称已存在")
        for field, value in data.items():
            setattr(unit, field, value)
        await self.db.commit()
        await self.db.refresh(unit)
        return UnitOut.model_validate(unit)

    async def delete_unit(self, unit_id: int) -> None:
        unit = await self._get_active(unit_id)
        if await self.units.count_goods(unit_id) > 0:
            raise AppException(ErrorCode.BAD_REQUEST, "该单位下存在商品，无法删除")
        unit.is_deleted = True
        await self.db.commit()

    async def _get_active(self, unit_id: int) -> BdUnit:
        unit = await self.units.get(unit_id)
        if unit is None or unit.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "计量单位不存在")
        return unit
