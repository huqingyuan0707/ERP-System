"""采购申请业务逻辑（P-01）：草稿 CRUD、提交、审核、数据范围与操作日志。

数据闭环：
  pur_apply + pur_apply_item → 状态流转（草稿 0 → 待审 1 → 已审 2，驳回回草稿 0）
  → 采购员参考已审申请建采购订单（V1 不自动转单）。
  本模块**不写库存（inv_stock / inv_stock_flow）、不写财务（fin_payable / fin_receivable）**，
  仅在 pur_apply、pur_apply_item 与 sys_oper_log 三处落库。

敏感点（事务 / 锁 / 状态机）：
  1. 提交、审核、修改草稿都先对主表加行锁（get_with_items(for_update=True)），
     保证「读状态 → 校验 → 改状态」在并发请求下不被穿插，避免重复审核/重复提交。
  2. 事务边界在 Service：写方法带 commit=True 时自行提交，端点用幂等包装时传 commit=False。
  3. 修改草稿的明细采用「整行软删 + 新增」，历史明细保留（is_deleted=True），不物理删除。
  4. 驳回原因写 audit_remark，重新提交时清空，保证「驳回原因只在被驳回后可见」。
"""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import ScopeContext, allows, apply_scope, resolve_scope
from app.core.exceptions import AppException, ErrorCode
from app.models.base_data import BdGoods, BdUnit
from app.models.pur import PurApply, PurApplyItem
from app.models.sys import SysDept, SysUser
from app.repositories.purchase_apply import PurApplyRepository
from app.schemas.common import PageResult
from app.schemas.purchase_apply import (
    APPLY_STATUS_APPROVED,
    APPLY_STATUS_DRAFT,
    APPLY_STATUS_PENDING,
    AUDIT_RESULT_APPROVED,
    PurApplyAudit,
    PurApplyCreate,
    PurApplyItemIn,
    PurApplyItemOut,
    PurApplyOut,
    PurApplyQuery,
    PurApplyUpdate,
)
from app.services.audit import write_oper_log
from app.utils.bill_no import generate_bill_no
from app.utils.pagination import paginate_result


class PurchaseApplyService:
    """采购申请：查询 / 草稿新增修改删除 / 提交 / 审核。"""

    # 单号规则键：sys_config.billno.pur_apply = "PA,4"
    APPLY_BIZ_KEY = "pur_apply"
    # 操作日志模块名，前端日志页按此筛选
    OPER_MODULE = "采购管理"

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._repo = PurApplyRepository(db, tenant_id=tenant_id)

    # ---------- 查询 ----------

    async def list_applies(self, query: PurApplyQuery, user: SysUser) -> PageResult[PurApplyOut]:
        """分页查询申请单：先按角色数据范围过滤，再叠加状态/部门/日期/关键字条件。"""
        ctx = await resolve_scope(self._db, user)
        stmt = self._repo.build_query(query)
        # 数据范围：pur_apply 有 dept_id 列，优先按部门过滤（本人 / 本部门 / 全部）
        stmt = await apply_scope(self._db, stmt, PurApply, ctx, dept_id_column=PurApply.dept_id)
        rows, total = await self._repo.paginate(stmt, query.page, query.page_size)
        items = await self._to_out_list(rows, with_items=False)
        return paginate_result(items, total, query.page, query.page_size)

    async def get_apply(self, apply_id: int, user: SysUser) -> PurApplyOut:
        """详情（含明细）：单据必须落在当前用户可见数据范围内，否则 403。"""
        ctx = await resolve_scope(self._db, user)
        apply = await self._get_visible(apply_id, ctx, for_update=False)
        return (await self._to_out_list([apply], with_items=True))[0]

    # ---------- 草稿新增 / 修改 / 删除 ----------

    async def create_apply(
        self, payload: PurApplyCreate, user: SysUser, *, commit: bool = True
    ) -> PurApplyOut:
        """新建采购申请（落库即草稿）：自动取号，申请人/部门默认当前用户。"""
        try:
            applicant_id = payload.applicant_id or user.id
            dept_id = payload.dept_id or user.dept_id
            if dept_id is None:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR, "当前账号未设置部门，请先指定申请部门"
                )
            await self._assert_dept_exists(dept_id)
            items = await self._load_items(payload.items)
            # 取号与单号查重共用事务锁，避免并发生成重复单号（PG advisory lock）
            await self._repo.lock_for_write()
            apply_no = await generate_bill_no(self._db, self.APPLY_BIZ_KEY)
            apply = PurApply(
                apply_no=apply_no,
                dept_id=dept_id,
                applicant_id=applicant_id,
                need_date=payload.need_date,
                status=APPLY_STATUS_DRAFT,
                remark=payload.remark,
                created_by=user.id,
                updated_by=user.id,
            )
            await self._repo.add(apply)
            await self._replace_items(apply.id, items, user.id)
            await self._write_log(user.id, "新增采购申请", apply.id, apply.apply_no)
            created = await self._repo.get_with_items(apply.id)
            if created is None:  # 防御：插入后立刻回查失败说明事务异常
                raise AppException(ErrorCode.SERVER_ERROR, "采购申请创建失败")
            result = (await self._to_out_list([created], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def update_apply(
        self,
        apply_id: int,
        payload: PurApplyUpdate,
        user: SysUser,
        *,
        commit: bool = True,
    ) -> PurApplyOut:
        """修改草稿：仅 status=0 可改，待审/已审一律 409，需先由审核人驳回回到草稿。"""
        try:
            ctx = await resolve_scope(self._db, user)
            apply = await self._get_visible(apply_id, ctx, for_update=True)
            if apply.status != APPLY_STATUS_DRAFT:
                status_text = "待审" if apply.status == APPLY_STATUS_PENDING else "已审"
                raise AppException(
                    ErrorCode.CONFLICT, f"申请单当前为「{status_text}」，只有草稿状态可以修改"
                )
            data = payload.model_dump(exclude_unset=True)
            if "dept_id" in data:
                dept_id = data["dept_id"]
                if dept_id is None:
                    raise AppException(ErrorCode.VALIDATION_ERROR, "申请部门不能为 null")
                await self._assert_dept_exists(dept_id)
                apply.dept_id = dept_id
            if "need_date" in data:
                need_date = data["need_date"]
                if need_date is None:
                    raise AppException(ErrorCode.VALIDATION_ERROR, "需求日期不能为 null")
                apply.need_date = need_date
            if "remark" in data:
                # 备注允许显式传 null 表示清空
                apply.remark = data["remark"]
            if payload.items is not None:
                # 明细整单替换：旧行软删保留流水，新行插入（ERP 要求留痕，不物理删历史）
                items = await self._load_items(payload.items)
                await self._replace_items(apply.id, items, user.id)
            apply.updated_by = user.id
            # 乐观锁 +1：并发修改同一草稿时，后提交者与前置读取的版本不一致可被识别
            apply.version += 1
            await self._write_log(user.id, "修改采购申请", apply.id, apply.apply_no)
            await self._db.flush()
            refreshed = await self._repo.get_with_items(apply.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "采购申请不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def delete_apply(self, apply_id: int, user: SysUser, *, commit: bool = True) -> None:
        """删除草稿：仅 status=0 可删，软删主表并同步软删明细，历史流水保留。"""
        try:
            ctx = await resolve_scope(self._db, user)
            apply = await self._get_visible(apply_id, ctx, for_update=True)
            if apply.status != APPLY_STATUS_DRAFT:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的申请单可以删除")
            apply.is_deleted = True
            apply.updated_by = user.id
            await self._repo.soft_delete_items(apply.id, user.id)
            await self._write_log(user.id, "删除采购申请", apply.id, apply.apply_no)
            if commit:
                await self._db.commit()
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 提交 / 审核 ----------

    async def submit_apply(
        self, apply_id: int, user: SysUser, *, commit: bool = True
    ) -> PurApplyOut:
        """提交申请：草稿 0 → 待审 1；提交前复核商品启用与数量，作为进入审批的最后一道校验。"""
        try:
            ctx = await resolve_scope(self._db, user)
            apply = await self._get_visible(apply_id, ctx, for_update=True)
            if apply.status != APPLY_STATUS_DRAFT:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的申请单可以提交")
            items_map = await self._repo.list_items([int(apply.id)])
            active_items = [
                item for item in items_map.get(int(apply.id), []) if not item.is_deleted
            ]
            if not active_items:
                raise AppException(ErrorCode.VALIDATION_ERROR, "申请单没有明细行，不能提交")
            await self._assert_items_submittable(active_items)
            apply.status = APPLY_STATUS_PENDING
            # 重新提交时清空上一次驳回原因，避免历史意见误导审核人
            apply.audit_remark = None
            apply.updated_by = user.id
            apply.version += 1
            await self._write_log(user.id, "提交采购申请", apply.id, apply.apply_no)
            await self._db.flush()
            refreshed = await self._repo.get_with_items(apply.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "采购申请不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def audit_apply(
        self, apply_id: int, payload: PurApplyAudit, user: SysUser, *, commit: bool = True
    ) -> PurApplyOut:
        """审核：待审 1 →（通过）已审 2 /（驳回）草稿 0，驳回原因写入 audit_remark。

        风险点：审核只推进状态，不改写明细数量、不写库存、不写财务，
        保证「审核后的申请内容」与申请人提交时完全一致，后续采购订单据此建单可追溯。
        """
        try:
            ctx = await resolve_scope(self._db, user)
            apply = await self._get_visible(apply_id, ctx, for_update=True)
            if apply.status != APPLY_STATUS_PENDING:
                raise AppException(ErrorCode.CONFLICT, "只有待审状态的申请单可以审核")
            remark = (payload.remark or "").strip() or None
            if payload.result == AUDIT_RESULT_APPROVED:
                apply.status = APPLY_STATUS_APPROVED
                action = "审核通过采购申请"
            else:
                apply.status = APPLY_STATUS_DRAFT
                action = "驳回采购申请"
            # 通过时保留审核意见，驳回时必填原因（Schema 已校验非空）
            apply.audit_remark = remark
            apply.audit_by = user.id
            # 模型 audit_at 为 DATE 字段（只到天），精确时间以 sys_oper_log.created_at 为准
            apply.audit_at = date.today()
            apply.updated_by = user.id
            apply.version += 1
            await self._write_log(
                user.id,
                action,
                apply.id,
                apply.apply_no,
                extra={"result": payload.result, "remark": remark},
            )
            await self._db.flush()
            refreshed = await self._repo.get_with_items(apply.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "采购申请不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 内部辅助 ----------

    async def _get_visible(self, apply_id: int, ctx: ScopeContext, *, for_update: bool) -> PurApply:
        """按 ID 取单据并做数据范围校验；不存在 404，越权 403。

        for_update=True 时对主表加行锁，提交/审核/修改/删除都走这里。
        """
        apply = await self._repo.get_with_items(apply_id, for_update=for_update)
        if apply is None or apply.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "采购申请不存在")
        if not await allows(self._db, ctx, created_by=apply.created_by, dept_id=apply.dept_id):
            raise AppException(ErrorCode.FORBIDDEN, "无权访问该采购申请")
        return apply

    async def _assert_dept_exists(self, dept_id: int) -> None:
        """校验申请部门存在且启用，避免把单据挂到已删除/停用的部门上。"""
        dept = await self._db.scalar(
            select(SysDept).where(
                SysDept.id == dept_id,
                SysDept.tenant_id == self._tenant_id,
                SysDept.is_deleted.is_(False),
            )
        )
        if dept is None:
            raise AppException(ErrorCode.VALIDATION_ERROR, "申请部门不存在")
        if dept.status != 1:
            raise AppException(ErrorCode.VALIDATION_ERROR, "申请部门已停用")

    async def _load_items(self, items: list[PurApplyItemIn]) -> list[PurApplyItemIn]:
        """批量校验商品：一次 IN 查询取回全部商品，避免逐行查库（N+1）。

        规则：商品必须存在、未删除、启用；停用商品不允许出现在新申请里。
        """
        goods_ids = {item.goods_id for item in items}
        stmt = select(BdGoods).where(
            BdGoods.id.in_(goods_ids),
            BdGoods.tenant_id == self._tenant_id,
            BdGoods.is_deleted.is_(False),
        )
        goods_map = {int(row.id): row for row in (await self._db.execute(stmt)).scalars().all()}
        for item in items:
            goods = goods_map.get(item.goods_id)
            if goods is None:
                raise AppException(ErrorCode.VALIDATION_ERROR, f"商品不存在: {item.goods_id}")
            if goods.status != 1:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR, f"商品「{goods.name}」已停用，不能申请采购"
                )
            if item.qty <= 0:  # Schema 已拦截，双保险防止绕过入口写入非法数量
                raise AppException(ErrorCode.VALIDATION_ERROR, "申请数量必须大于 0")
        return items

    async def _assert_items_submittable(self, items: list[PurApplyItem]) -> None:
        """提交前复核明细：数量 > 0、商品仍启用（草稿期间商品可能被停用）。"""
        goods_ids = {int(row.goods_id) for row in items}
        stmt = select(BdGoods.id, BdGoods.name, BdGoods.status).where(
            BdGoods.id.in_(goods_ids),
            BdGoods.tenant_id == self._tenant_id,
            BdGoods.is_deleted.is_(False),
        )
        rows = (await self._db.execute(stmt)).all()
        status_map = {int(row[0]): (str(row[1]), int(row[2])) for row in rows}
        for row in items:
            goods_id = int(row.goods_id)
            if goods_id not in status_map:
                raise AppException(ErrorCode.VALIDATION_ERROR, f"商品不存在: {goods_id}")
            name, status = status_map[goods_id]
            if status != 1:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR, f"商品「{name}」已停用，请删除该行后重新提交"
                )
            if Decimal(row.qty) <= 0:
                raise AppException(ErrorCode.VALIDATION_ERROR, "申请数量必须大于 0")

    async def _replace_items(
        self, apply_id: int, items: list[PurApplyItemIn], operator_id: int
    ) -> None:
        """明细整单替换：先软删旧行（保留流水），再批量插入新行。"""
        await self._repo.soft_delete_items(apply_id, operator_id)
        for item in items:
            self._db.add(
                PurApplyItem(
                    apply_id=apply_id,
                    goods_id=item.goods_id,
                    qty=item.qty,
                    need_date=item.need_date,
                    tenant_id=self._tenant_id,
                    created_by=operator_id,
                    updated_by=operator_id,
                )
            )
        await self._db.flush()

    async def _to_out_list(self, rows: list[PurApply], *, with_items: bool) -> list[PurApplyOut]:
        """批量转换出参：部门名、申请人/审核人姓名、明细行数、商品信息一次查完，避免 N+1。

        with_items=True 时额外批量查明细与商品单位；False 时只做行数统计（列表页）。
        """
        if not rows:
            return []
        apply_ids = [int(row.id) for row in rows]
        items_map = await self._repo.list_items(apply_ids)
        dept_ids = {row.dept_id for row in rows}
        user_ids = {row.applicant_id for row in rows}
        user_ids |= {row.audit_by for row in rows if row.audit_by is not None}

        dept_names: dict[int, str] = {}
        if dept_ids:
            dept_rows = (
                await self._db.execute(
                    select(SysDept.id, SysDept.name).where(
                        SysDept.id.in_(dept_ids), SysDept.tenant_id == self._tenant_id
                    )
                )
            ).all()
            dept_names = {int(row[0]): str(row[1]) for row in dept_rows}

        user_names: dict[int, str] = {}
        if user_ids:
            user_rows = (
                await self._db.execute(
                    select(SysUser.id, SysUser.real_name, SysUser.username).where(
                        SysUser.id.in_(user_ids), SysUser.tenant_id == self._tenant_id
                    )
                )
            ).all()
            # 无真实姓名时回退登录账号，保证列表与打印抬头不出现空白
            user_names = {int(row[0]): str(row[1] or row[2]) for row in user_rows}

        # 行数直接由已批量查回的明细统计（含软删行需排除），无需再发 count 查询
        goods_map, unit_names = await self._load_goods_info(items_map) if with_items else ({}, {})

        result: list[PurApplyOut] = []
        for row in rows:
            row_items = items_map.get(int(row.id), [])
            active_items = [item for item in row_items if not item.is_deleted]
            item_outs = (
                self._build_item_outs(active_items, goods_map, unit_names) if with_items else []
            )
            result.append(
                PurApplyOut.model_validate(row).model_copy(
                    update={
                        "dept_name": dept_names.get(row.dept_id),
                        "applicant_name": user_names.get(row.applicant_id),
                        "audit_by_name": user_names.get(row.audit_by) if row.audit_by else None,
                        "items_count": len(active_items),
                        "items": item_outs,
                    }
                )
            )
        return result

    @staticmethod
    def _build_item_outs(
        items: list[PurApplyItem],
        goods_map: dict[int, BdGoods],
        unit_names: dict[int, str],
    ) -> list[PurApplyItemOut]:
        """把明细行转换成出参（只处理未软删行）；商品编码/名称/规格/单位来自批量查询结果。"""
        result: list[PurApplyItemOut] = []
        for item in items:
            goods = goods_map.get(int(item.goods_id))
            unit_name = None
            if goods is not None and goods.unit_id is not None:
                unit_name = unit_names.get(int(goods.unit_id))
            result.append(
                PurApplyItemOut(
                    id=item.id,
                    goods_id=item.goods_id,
                    goods_code=goods.code if goods else None,
                    goods_name=goods.name if goods else None,
                    spec=goods.spec if goods else None,
                    unit_name=unit_name,
                    qty=item.qty,
                    need_date=item.need_date,
                )
            )
        return result

    async def _load_goods_info(
        self, items_map: dict[int, list[PurApplyItem]]
    ) -> tuple[dict[int, BdGoods], dict[int, str]]:
        """一次性加载明细涉及的商品与单位名称（详情/打印用），避免逐行查商品。"""
        goods_ids = {
            int(item.goods_id)
            for items in items_map.values()
            for item in items
            if not item.is_deleted
        }
        if not goods_ids:
            return {}, {}
        goods_rows = (
            await self._db.execute(
                select(BdGoods).where(
                    BdGoods.id.in_(goods_ids), BdGoods.tenant_id == self._tenant_id
                )
            )
        ).scalars()
        goods_map = {int(goods.id): goods for goods in goods_rows.all()}
        unit_ids = {int(goods.unit_id) for goods in goods_map.values() if goods.unit_id}
        unit_names: dict[int, str] = {}
        if unit_ids:
            unit_rows = (
                await self._db.execute(
                    select(BdUnit.id, BdUnit.name).where(
                        BdUnit.id.in_(unit_ids), BdUnit.tenant_id == self._tenant_id
                    )
                )
            ).all()
            unit_names = {int(row[0]): str(row[1]) for row in unit_rows}
        return goods_map, unit_names

    async def _write_log(
        self,
        operator_id: int,
        action: str,
        apply_id: int,
        apply_no: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """写操作日志：与业务同一事务提交，保证「有单必有日志」。

        audit_at 只有日期，同一天内的提交/驳回/审核先后顺序以本表 created_at 为准，
        因此审核链路逐条记录 action，供后续追溯与对账排查使用。
        """
        params: dict[str, Any] = {"applyId": apply_id, "applyNo": apply_no}
        if extra:
            params.update(extra)
        await write_oper_log(
            self._db,
            tenant_id=self._tenant_id,
            operator_id=operator_id,
            action=action,
            params=params,
            module=self.OPER_MODULE,
        )
