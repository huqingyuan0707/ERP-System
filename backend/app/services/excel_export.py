"""租户隔离的商品导入、完整分批 Excel 导出与本地任务执行。"""

import json
import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ErrorCode
from app.models.base_data import BdGoods, BdGoodsCategory, BdUnit, BdWarehouse
from app.models.inventory import InvStock
from app.models.sys import SysTask
from app.repositories.goods import GoodsRepository
from app.repositories.task import TaskRepository
from app.schemas.goods import GoodsCreate, GoodsImportErrorItem, GoodsImportResult, GoodsQuery
from app.schemas.task import TaskOut
from app.services.audit import write_oper_log
from app.services.goods import GoodsService
from app.utils.excel import ExcelWriter
from app.utils.importer import parse_import_rows, to_decimal, to_str


logger = logging.getLogger(__name__)
EXPORT_DIR = Path("exports")
EXPORT_BATCH_SIZE = 1000
FIELD_HEADERS = {
    "code": "商品编码",
    "name": "商品名称",
    "spec": "规格",
    "category_id": "分类名称",
    "unit_id": "单位名称",
    "barcode": "条码",
    "cost_price": "成本价",
    "sale_price": "售价",
    "stock_lower": "库存下限",
    "stock_upper": "库存上限",
    "status": "状态",
    "remark": "备注",
}


class ExcelExportService:
    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.tasks = TaskRepository(db, tenant_id)

    async def _execute_export(
        self,
        task_type: str,
        params: dict,
        created_by: int,
        run: Callable[[str], Awaitable[Path]],
    ) -> TaskOut:
        task_id = uuid.uuid4().hex
        task = SysTask(
            tenant_id=self.tenant_id,
            task_id=task_id,
            task_type=task_type,
            status=2,
            params=json.dumps(params, ensure_ascii=False),
            progress=0,
            created_by=created_by,
            started_at=datetime.now(UTC),
        )
        self.db.add(task)
        await self.db.commit()
        file_path: Path | None = None
        try:
            file_path = await run(task_id)
            task = await self.tasks.get_by_task_id(task_id)
            if task is None:
                raise RuntimeError("任务不存在")
            task.status = 3
            task.progress = 100
            task.file_path = file_path.as_posix()
            task.finished_at = datetime.now(UTC)
            await write_oper_log(
                self.db,
                tenant_id=self.tenant_id,
                operator_id=created_by,
                action="导出",
                params={"task_id": task_id, "task_type": task_type, **params},
            )
            await self.db.commit()
        except Exception as exc:
            await self.db.rollback()
            if file_path is not None:
                file_path.unlink(missing_ok=True)
            logger.exception("导出任务失败 tenant=%s task=%s", self.tenant_id, task_id)
            task = await self.tasks.get_by_task_id(task_id)
            if task is None:
                raise AppException(ErrorCode.SERVER_ERROR, "任务不存在") from exc
            task.status = 4
            task.progress = 0
            task.file_path = None
            task.error_msg = (
                str(exc) if isinstance(exc, (AppException, ValueError)) else "导出失败，请稍后重试"
            )
            task.finished_at = datetime.now(UTC)
            await write_oper_log(
                self.db,
                tenant_id=self.tenant_id,
                operator_id=created_by,
                action="导出",
                status=0,
                result="export failed",
                params={"task_id": task_id},
            )
            await self.db.commit()
        task = await self.tasks.get_by_task_id(task_id)
        return TaskOut.model_validate(task).model_copy(
            update={
                "can_download": task.status == 3 and bool(task.file_path),
            }
        )

    async def _category_maps(self) -> tuple[dict[int, str], dict[str, list[int]]]:
        rows = list(
            (
                await self.db.execute(
                    select(BdGoodsCategory).where(
                        BdGoodsCategory.tenant_id == self.tenant_id,
                        BdGoodsCategory.is_deleted.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        by_id = {row.id: row for row in rows}
        paths: dict[int, str] = {}
        names: dict[str, list[int]] = defaultdict(list)
        for row in rows:
            parts: list[str] = []
            seen: set[int] = set()
            current = row
            while current is not None and current.id not in seen:
                seen.add(current.id)
                parts.append(current.name)
                if current.parent_id is None:
                    paths[row.id] = "/".join(reversed(parts))
                    names[row.name].append(row.id)
                    break
                current = by_id.get(current.parent_id)
            # 损坏/跨租户的祖先链不输出，也不可借单名解析绕过。
        return paths, names

    async def export_goods(self, created_by: int, query: GoodsQuery) -> TaskOut:
        return await self._execute_export(
            "goods_export",
            {"keyword": query.keyword, "category_id": query.category_id, "status": query.status},
            created_by,
            lambda task_id: self._run_goods_export(task_id, query),
        )

    async def _run_goods_export(self, task_id: str, query: GoodsQuery) -> Path:
        paths, _ = await self._category_maps()
        # build_query 与商品列表共享筛选；仓储已对商品和关联表分别实施租户隔离。
        stmt = GoodsRepository(self.db, self.tenant_id).build_query(query).order_by(None)
        stmt = stmt.where(BdGoods.tenant_id == self.tenant_id).order_by(BdGoods.id)
        writer = ExcelWriter(
            [
                "商品编码",
                "商品名称",
                "规格",
                "分类名称",
                "单位名称",
                "条码",
                "成本价",
                "售价",
                "库存下限",
                "库存上限",
                "状态",
                "备注",
            ],
            "商品导出",
        )
        path = EXPORT_DIR / f"goods_export_{task_id}.xlsx"
        try:
            last_id = 0
            while True:
                rows = list(
                    (
                        await self.db.execute(
                            stmt.where(BdGoods.id > last_id).limit(EXPORT_BATCH_SIZE),
                        )
                    ).all()
                )
                if not rows:
                    break
                for goods, _, unit_name in rows:
                    writer.append(
                        [
                            goods.code,
                            goods.name,
                            goods.spec or "",
                            paths.get(goods.category_id, ""),
                            unit_name or "",
                            goods.barcode or "",
                            # Excel 数值精度只有15位；十进制定点文本可精确往返，不经 float。
                            str(goods.cost_price),
                            str(goods.sale_price),
                            str(goods.stock_lower),
                            str(goods.stock_upper),
                            "启用" if goods.status == 1 else "停用",
                            goods.remark or "",
                        ]
                    )
                last_id = rows[-1][0].id
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            writer.save(path)
            return path
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            writer.close()

    async def export_stocks(
        self,
        created_by: int,
        warehouse_id: int | None = None,
        keyword: str | None = None,
    ) -> TaskOut:
        return await self._execute_export(
            "stock_export",
            {"warehouse_id": warehouse_id, "keyword": keyword},
            created_by,
            lambda task_id: self._run_stocks_export(task_id, warehouse_id, keyword),
        )

    async def _run_stocks_export(
        self,
        task_id: str,
        warehouse_id: int | None,
        keyword: str | None,
    ) -> Path:
        stmt = (
            select(InvStock, BdGoods, BdWarehouse, BdUnit.name)
            .join(
                BdGoods,
                and_(
                    InvStock.goods_id == BdGoods.id,
                    BdGoods.tenant_id == self.tenant_id,
                    BdGoods.is_deleted.is_(False),
                ),
            )
            .join(
                BdWarehouse,
                and_(
                    InvStock.warehouse_id == BdWarehouse.id,
                    BdWarehouse.tenant_id == self.tenant_id,
                    BdWarehouse.is_deleted.is_(False),
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
            .where(InvStock.tenant_id == self.tenant_id, InvStock.is_deleted.is_(False))
            .order_by(InvStock.id)
        )
        if warehouse_id is not None:
            stmt = stmt.where(InvStock.warehouse_id == warehouse_id)
        if keyword:
            stmt = stmt.where(BdGoods.name.ilike(f"%{keyword}%"))
        writer = ExcelWriter(
            [
                "仓库编码",
                "仓库名称",
                "商品编码",
                "商品名称",
                "规格",
                "单位名称",
                "账面数量",
                "锁定数量",
                "可用数量",
                "平均成本",
                "库存金额",
            ],
            "库存导出",
        )
        path = EXPORT_DIR / f"stock_export_{task_id}.xlsx"
        try:
            last_id = 0
            while True:
                rows = list(
                    (
                        await self.db.execute(
                            stmt.where(InvStock.id > last_id).limit(EXPORT_BATCH_SIZE),
                        )
                    ).all()
                )
                if not rows:
                    break
                for stock, goods, warehouse, unit_name in rows:
                    writer.append(
                        [
                            warehouse.code,
                            warehouse.name,
                            goods.code,
                            goods.name,
                            goods.spec or "",
                            unit_name or "",
                            str(stock.qty),
                            str(stock.locked_qty),
                            str(stock.available_qty),
                            str(stock.avg_cost),
                            str(stock.qty * stock.avg_cost),
                        ]
                    )
                last_id = rows[-1][0].id
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            writer.save(path)
            return path
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            writer.close()

    async def export_report(self, created_by: int, report_type: str) -> TaskOut:
        # 原接口只有占位文字，不能把占位文件当成真实报表导出成功。
        raise AppException(ErrorCode.BAD_REQUEST, "通用报表数据尚未接入，暂不支持导出")

    async def export_statement(self, created_by: int, statement_id: int) -> TaskOut:
        raise AppException(ErrorCode.BAD_REQUEST, "对账单数据尚未接入，暂不支持导出")

    async def import_goods(self, content: bytes, created_by: int) -> GoodsImportResult:
        parsed = parse_import_rows(content)
        if not parsed:
            raise AppException(ErrorCode.VALIDATION_ERROR, "文件为空或无有效数据行")
        paths, category_names = await self._category_maps()
        category_paths: dict[str, list[int]] = defaultdict(list)
        for category_id, path in paths.items():
            category_paths[path].append(category_id)
        units: dict[str, list[int]] = defaultdict(list)
        for unit in (
            await self.db.execute(
                select(BdUnit).where(
                    BdUnit.tenant_id == self.tenant_id,
                    BdUnit.is_deleted.is_(False),
                )
            )
        ).scalars():
            units[unit.name].append(unit.id)

        errors: list[GoodsImportErrorItem] = []
        prepared: list[BdGoods] = []
        seen_codes: set[str] = set()
        seen_barcodes: set[str] = set()
        service = GoodsService(self.db, self.tenant_id)
        for excel_row, item in parsed:
            row_errors: list[str] = []
            payload: dict = {
                field: to_str(item.get(header)) or None
                for field, header in FIELD_HEADERS.items()
                if field in {"code", "spec", "barcode", "remark"}
            }
            payload["name"] = to_str(item.get("商品名称"))
            code, barcode = payload["code"], payload["barcode"]
            if not code:
                row_errors.append("商品编码：不能为空")
            elif code in seen_codes:
                row_errors.append("商品编码：文件内重复")
            if barcode and barcode in seen_barcodes:
                row_errors.append("条码：文件内重复")
            if code:
                seen_codes.add(code)
            if barcode:
                seen_barcodes.add(barcode)
            for field, options in (("category_id", category_names), ("unit_id", units)):
                header = FIELD_HEADERS[field]
                name = to_str(item.get(header))
                if field == "category_id" and "/" in name:
                    options = category_paths
                ids = options.get(name, []) if name else []
                payload[field] = ids[0] if len(ids) == 1 else None
                if name and not ids:
                    row_errors.append(f"{header}：{name} 不存在，请先维护")
                elif name and len(ids) != 1:
                    row_errors.append(f"{header}：{name} 存在歧义，请填写完整分类路径")
            for field in ("cost_price", "sale_price", "stock_lower", "stock_upper"):
                header = FIELD_HEADERS[field]
                try:
                    payload[field] = to_decimal(item.get(header))
                except ValueError as exc:
                    row_errors.append(f"{header}：{exc}")
            status = to_str(item.get("状态"))
            if status in {"", "启用", "1"}:
                payload["status"] = 1
            elif status in {"停用", "0"}:
                payload["status"] = 0
            else:
                row_errors.append("状态：只能填写启用/停用或1/0")
            try:
                goods_payload = GoodsCreate.model_validate(payload)
            except ValidationError as exc:
                for error in exc.errors():
                    field = str(error["loc"][0]) if error["loc"] else "商品"
                    row_errors.append(f"{FIELD_HEADERS.get(field, field)}：{error['msg']}")
            else:
                if not row_errors:
                    try:
                        prepared.append(await service.prepare_create(goods_payload, created_by))
                    except AppException as exc:
                        row_errors.append(exc.message)
            if row_errors:
                errors.append(GoodsImportErrorItem(row=excel_row, errors=row_errors))
        if errors:
            await write_oper_log(
                self.db,
                tenant_id=self.tenant_id,
                operator_id=created_by,
                action="导入商品",
                status=0,
                result="validation failed",
                params={"total": len(parsed), "fail_count": len(errors)},
            )
            await self.db.commit()
            return GoodsImportResult(total=len(parsed), fail_count=len(errors), errors=errors)
        try:
            self.db.add_all(prepared)
            await self.db.flush()
            await write_oper_log(
                self.db,
                tenant_id=self.tenant_id,
                operator_id=created_by,
                action="导入商品",
                params={"total": len(parsed), "success_count": len(prepared)},
            )
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise AppException(
                ErrorCode.CONFLICT, "商品编码或条码发生并发冲突，整批未入库，请检查后重试"
            ) from exc
        except Exception:
            await self.db.rollback()
            raise
        return GoodsImportResult(success=True, total=len(parsed), success_count=len(prepared))
