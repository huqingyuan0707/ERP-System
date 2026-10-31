"""Excel 导入导出服务（C-07）。

负责将导出请求转化为异步任务，生成 Excel 文件，供前端轮询任务状态并下载。
本期使用本地文件存储（生产环境可替换为 MinIO / 对象存储）。
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ErrorCode
from app.models.base_data import BdGoods, BdGoodsCategory, BdUnit, BdWarehouse
from app.models.inventory import InvStock
from app.models.sys import SysTask
from app.repositories.goods import GoodsRepository
from app.repositories.task import TaskRepository
from app.schemas.goods import GoodsImportErrorItem, GoodsImportResult, GoodsQuery
from app.schemas.task import TaskOut
from app.utils.excel import build_workbook
from app.utils.importer import parse_import_rows, to_float, to_str


# 本地导出文件目录（开发环境；生产建议挂载 MinIO / 对象存储）
EXPORT_DIR = Path("exports")
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# 单次导出上限与导入行数上限，避免一次性生成/写入过大文件
EXPORT_LIMIT = 10000
IMPORT_MAX_ROWS = 5000


class ExcelExportService:
    """Excel 导入导出业务服务。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.tasks = TaskRepository(db)

    # ------------------------------------------------------------------
    # 内部辅助：创建任务记录
    # ------------------------------------------------------------------
    async def _create_task_record(
        self, task_type: str, params: dict | None, created_by: int | None
    ) -> str:
        """在当前会话内插入一条状态为“待执行”的任务，返回 task_id。"""
        task = SysTask(
            task_id=uuid.uuid4().hex,
            task_type=task_type,
            status=1,  # 待执行
            params=json.dumps(params, ensure_ascii=False) if params else None,
            progress=0,
            created_by=created_by,
        )
        self.db.add(task)
        await self.db.flush()
        await self.db.refresh(task)
        return task.task_id

    async def _mark_done(
        self,
        task_id: str,
        *,
        file_path: str | None = None,
        error_msg: str | None = None,
        progress: int = 100,
    ) -> None:
        """将任务标记为成功或失败。"""
        task = await self.tasks.get_by_task_id(task_id)
        if task is None:
            return
        task.status = 3 if error_msg is None else 4
        task.file_path = file_path
        task.error_msg = error_msg
        task.progress = progress
        task.finished_at = datetime.now(UTC)
        await self.db.commit()

    async def _task_out(self, task_id: str) -> TaskOut:
        """回查任务记录并转为出参，保证接口响应与 TaskOut 模型一致。"""
        task = await self.tasks.get_by_task_id(task_id)
        if task is None:
            raise AppException(ErrorCode.SERVER_ERROR, "任务创建失败")
        return TaskOut.model_validate(task)

    # ------------------------------------------------------------------
    # 商品导出
    # ------------------------------------------------------------------
    async def export_goods(
        self,
        created_by: int | None,
        query: GoodsQuery,
    ) -> TaskOut:
        """提交商品导出异步任务，筛选条件与列表接口保持一致（关键字/分类/状态）。"""
        task_id = await self._create_task_record(
            "goods_export",
            {
                "keyword": query.keyword,
                "category_id": query.category_id,
                "status": query.status,
            },
            created_by,
        )
        try:
            await self._run_goods_export(task_id, query)
        except Exception as exc:  # noqa: BLE001
            await self._mark_done(task_id, error_msg=str(exc))
        await self.db.commit()
        return await self._task_out(task_id)

    async def _run_goods_export(self, task_id: str, query: GoodsQuery) -> None:
        """执行商品导出并写入 Excel，复用商品仓储保证筛选与列表页一致。"""
        repo = GoodsRepository(self.db)
        rows, _ = await repo.list_goods(query, limit=EXPORT_LIMIT)

        headers = [
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
        ]
        data = [
            [
                g.code,
                g.name,
                g.spec or "",
                category_name or "",
                unit_name or "",
                g.barcode or "",
                float(g.cost_price),
                float(g.sale_price),
                float(g.stock_lower),
                float(g.stock_upper),
                "启用" if g.status == 1 else "停用",
                g.remark or "",
            ]
            for g, category_name, unit_name in rows
        ]

        file_name = f"goods_export_{task_id}.xlsx"
        file_path = str(EXPORT_DIR / file_name)
        content = build_workbook(headers, data, sheet_title="商品导出")
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        Path(file_path).write_bytes(content)

        await self._mark_done(task_id, file_path=file_path, progress=100)

    # ------------------------------------------------------------------
    # 库存导出
    # ------------------------------------------------------------------
    async def export_stocks(
        self,
        created_by: int | None,
        warehouse_id: int | None = None,
        keyword: str | None = None,
    ) -> TaskOut:
        """提交库存导出异步任务。"""
        task_id = await self._create_task_record(
            "stock_export",
            {"warehouse_id": warehouse_id, "keyword": keyword},
            created_by,
        )
        try:
            await self._run_stocks_export(task_id, warehouse_id, keyword)
        except Exception as exc:  # noqa: BLE001
            await self._mark_done(task_id, error_msg=str(exc))
        await self.db.commit()
        return await self._task_out(task_id)

    async def _run_stocks_export(
        self, task_id: str, warehouse_id: int | None, keyword: str | None
    ) -> None:
        """执行库存导出并写入 Excel。"""
        stmt = (
            select(InvStock, BdGoods, BdWarehouse)
            .join(BdGoods, InvStock.goods_id == BdGoods.id)
            .join(BdWarehouse, InvStock.warehouse_id == BdWarehouse.id)
            .where(
                InvStock.is_deleted == False,  # noqa: E712
                BdGoods.is_deleted == False,  # noqa: E712
                BdWarehouse.is_deleted == False,  # noqa: E712
            )
        )
        if warehouse_id is not None:
            stmt = stmt.where(InvStock.warehouse_id == warehouse_id)
        if keyword:
            stmt = stmt.where(BdGoods.name.ilike(f"%{keyword}%"))
        stmt = stmt.order_by(BdWarehouse.code, BdGoods.code)

        rows = list((await self.db.execute(stmt)).all())

        headers = [
            "仓库编码",
            "仓库名称",
            "商品编码",
            "商品名称",
            "规格",
            "单位ID",
            "账面数量",
            "锁定数量",
            "可用数量",
            "平均成本",
            "库存金额",
        ]
        data = []
        for stock, goods, warehouse in rows:
            data.append(
                [
                    warehouse.code,
                    warehouse.name,
                    goods.code,
                    goods.name,
                    goods.spec or "",
                    goods.unit_id or "",
                    float(stock.qty),
                    float(stock.locked_qty),
                    float(stock.available_qty),
                    float(stock.avg_cost),
                    float(stock.qty) * float(stock.avg_cost),
                ]
            )

        file_name = f"stock_export_{task_id}.xlsx"
        file_path = str(EXPORT_DIR / file_name)
        content = build_workbook(headers, data, sheet_title="库存导出")
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        Path(file_path).write_bytes(content)

        await self._mark_done(task_id, file_path=file_path, progress=100)

    # ------------------------------------------------------------------
    # 通用报表导出
    # ------------------------------------------------------------------
    async def export_report(
        self, created_by: int | None, report_type: str
    ) -> TaskOut:
        """提交通用报表导出异步任务。"""
        task_id = await self._create_task_record(
            "report_export", {"report_type": report_type}, created_by
        )
        try:
            await self._run_report_export(task_id, report_type)
        except Exception as exc:  # noqa: BLE001
            await self._mark_done(task_id, error_msg=str(exc))
        await self.db.commit()
        return await self._task_out(task_id)

    async def _run_report_export(self, task_id: str, report_type: str) -> None:
        """执行报表导出（占位实现，实际数据由各报表 Service 提供）。"""
        headers = ["报表类型", "说明", "生成时间"]
        data = [
            [
                report_type,
                f"{report_type} 报表导出（占位，实际数据接入各报表 Service 后填充）",
                datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            ]
        ]
        file_name = f"report_{report_type}_{task_id}.xlsx"
        file_path = str(EXPORT_DIR / file_name)
        content = build_workbook(headers, data, sheet_title=f"{report_type}报表")
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        Path(file_path).write_bytes(content)
        await self._mark_done(task_id, file_path=file_path, progress=100)

    # ------------------------------------------------------------------
    # 对账单导出
    # ------------------------------------------------------------------
    async def export_statement(
        self, created_by: int | None, statement_id: int
    ) -> TaskOut:
        """提交对账单导出异步任务。"""
        task_id = await self._create_task_record(
            "statement_export", {"statement_id": statement_id}, created_by
        )
        try:
            await self._run_statement_export(task_id, statement_id)
        except Exception as exc:  # noqa: BLE001
            await self._mark_done(task_id, error_msg=str(exc))
        await self.db.commit()
        return await self._task_out(task_id)

    async def _run_statement_export(self, task_id: str, statement_id: int) -> None:
        """执行对账单导出（占位实现）。"""
        headers = ["对账单ID", "说明", "生成时间"]
        data = [
            [
                statement_id,
                "对账单导出（占位，实际数据接入 F-06 后填充）",
                datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            ]
        ]
        file_name = f"statement_{statement_id}_{task_id}.xlsx"
        file_path = str(EXPORT_DIR / file_name)
        content = build_workbook(headers, data, sheet_title="对账单导出")
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        Path(file_path).write_bytes(content)
        await self._mark_done(task_id, file_path=file_path, progress=100)

    # ------------------------------------------------------------------
    # 商品导入
    # ------------------------------------------------------------------
    async def import_goods(
        self, content: bytes, created_by: int | None
    ) -> GoodsImportResult:
        """解析上传的商品 Excel，逐行校验，全部通过才在单事务内批量入库。

        任一错误行都会返回错误报告且 0 行入库（C-07 全有或全无）；
        分类/单位需事先维护，导入时不隐式创建，避免错误数据污染基础档案。
        """
        parsed = parse_import_rows(content)
        if not parsed:
            raise AppException(ErrorCode.VALIDATION_ERROR, "文件为空或无有效数据行")
        if len(parsed) > IMPORT_MAX_ROWS:
            raise AppException(
                ErrorCode.VALIDATION_ERROR, f"单次导入不能超过 {IMPORT_MAX_ROWS} 行"
            )

        errors: list[GoodsImportErrorItem] = []
        valid_rows: list[dict[str, object]] = []

        # 预加载分类与单位名称映射（仅未删除），以及已占用编码/条码
        categories = {
            row.name: row.id
            for row in list(
                (
                    await self.db.execute(
                        select(BdGoodsCategory).where(
                            BdGoodsCategory.is_deleted.is_(False)
                        )
                    )
                ).scalars().all()
            )
        }
        units = {
            row.name: row.id
            for row in list(
                (
                    await self.db.execute(
                        select(BdUnit).where(BdUnit.is_deleted.is_(False))
                    )
                ).scalars().all()
            )
        }
        # 编码唯一约束包含已软删除记录，因此这里不过滤 is_deleted
        existing_codes = {
            row[0]
            for row in (await self.db.execute(select(BdGoods.code))).all()
        }
        existing_barcodes = {
            row[0]
            for row in (
                await self.db.execute(
                    select(BdGoods.barcode).where(BdGoods.barcode.is_not(None))
                )
            ).all()
        }

        for excel_row, item in parsed:
            row_errors: list[str] = []
            code = to_str(item.get("商品编码"))
            name = to_str(item.get("商品名称"))
            barcode = to_str(item.get("条码"))
            if not code:
                row_errors.append("商品编码不能为空")
            elif code in existing_codes:
                row_errors.append(f"商品编码 {code} 已存在")
            if not name:
                row_errors.append("商品名称不能为空")
            if barcode and barcode in existing_barcodes:
                row_errors.append(f"条码 {barcode} 已存在")

            cat_name = to_str(item.get("分类名称"))
            cat_id = categories.get(cat_name) if cat_name else None
            if cat_name and cat_id is None:
                row_errors.append(f"分类 {cat_name} 不存在，请先维护分类")

            unit_name = to_str(item.get("单位名称"))
            unit_id = units.get(unit_name) if unit_name else None
            if unit_name and unit_id is None:
                row_errors.append(f"单位 {unit_name} 不存在，请先维护单位")

            cost_price = to_float(item.get("成本价"))
            sale_price = to_float(item.get("售价"))
            stock_lower = to_float(item.get("库存下限"))
            stock_upper = to_float(item.get("库存上限"))
            if cost_price < 0:
                row_errors.append("成本价不能为负数")
            if sale_price < 0:
                row_errors.append("售价不能为负数")
            if stock_lower > stock_upper:
                row_errors.append("库存下限不能大于上限")

            if row_errors:
                errors.append(GoodsImportErrorItem(row=excel_row, errors=row_errors))
                continue

            valid_rows.append(
                {
                    "code": code,
                    "name": name,
                    "spec": to_str(item.get("规格")) or None,
                    "category_id": cat_id,
                    "unit_id": unit_id,
                    "barcode": barcode or None,
                    "cost_price": cost_price,
                    "sale_price": sale_price,
                    "stock_lower": stock_lower,
                    "stock_upper": stock_upper,
                    "status": 1,
                    "remark": to_str(item.get("备注")) or None,
                }
            )
            # 文件内重复校验：把本行编码/条码计入集合
            existing_codes.add(code)
            if barcode:
                existing_barcodes.add(barcode)

        if errors:
            return GoodsImportResult(
                success=False,
                total=len(parsed),
                success_count=0,
                fail_count=len(errors),
                errors=errors,
            )

        # 全部合法：单事务批量写入，任何一行失败都不会留下半成品数据
        for row in valid_rows:
            self.db.add(BdGoods(**row))
        await self.db.commit()

        return GoodsImportResult(
            success=True,
            total=len(parsed),
            success_count=len(valid_rows),
            fail_count=0,
            errors=[],
        )
