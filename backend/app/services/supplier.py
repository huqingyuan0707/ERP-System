"""供应商业务逻辑：编码、账期、引用保护、应付余额与操作日志。

本层统一提交或回滚事务，供应商资料、应付汇总和日志必须一起成功或一起撤销。
停用通过部分更新 status=0 完成，只影响新单据的可选范围，不影响历史单据。
"""

import json  # 将操作目标及变更明细写成 JSON，便于审计。
from datetime import date, timedelta  # 日期与天数差，用于按账期推算应付到期日。
from decimal import Decimal  # 定点数类型，应付余额使用金额类型。

from sqlalchemy.ext.asyncio import AsyncSession  # 所有仓储共用请求内的异步会话。

from app.core.exceptions import AppException, ErrorCode  # 复用统一业务异常和 HTTP 错误码。
from app.models.base_data import BdSupplier  # 供应商 ORM 对象，仅在业务层内部使用。
from app.models.sys import SysOperLog  # 操作日志和供应商写入同一个数据库事务。
from app.repositories.oper_log import OperLogRepository  # 日志仓储只 flush，不单独提交。
from app.repositories.supplier import SupplierRepository  # 封装供应商查询与单据引用检查。
from app.schemas.common import PageResult  # 列表统一使用项目分页结构。
from app.schemas.supplier import (  # 请求与返回均使用供应商专用 Schema。
    SupplierCreate,  # 新增请求，允许省略编码。
    SupplierOut,  # 返回给调用方的供应商数据，不直接暴露 ORM。
    SupplierQuery,  # 列表的分页、关键字和状态条件。
    SupplierUpdate,  # 部分更新请求，区分未传字段与显式传空。
)  # Schema 已负责长度、账期和状态范围等基础输入校验。
from app.utils.code_gen import format_code, next_seq_from_code  # 使用统一编码工具。
from app.utils.pagination import paginate_result  # 封装当前页数据及总条数。


SUPPLIER_CODE_PREFIX = "SUP"  # 供应商编码统一使用 SUP 前缀，如 SUP0001。
OPER_MODULE = "基础资料"  # sys_oper_log 中记录本业务所属模块。


class SupplierService:  # 汇集供应商业务规则，接口层只负责调用。
    """提供供应商查询和写操作；删除前检查采购与应付引用，停用只影响新单据选择。"""

    def __init__(self, db: AsyncSession) -> None:  # 接收请求级会话，不创建额外事务。
        """用 db 初始化全部仓储；它们共享同一事务，无返回值。"""
        self.db = db  # 提交和回滚统一由本 Service 控制。
        self.suppliers = SupplierRepository(db)  # 访问供应商及外部单据引用。
        self.oper_logs = OperLogRepository(db)  # 写入与供应商变更绑定的审计记录。

    async def list_suppliers(  # 分页查询供应商并一次性补齐应付余额。
        self,  # 使用当前供应商 Service。
        query: SupplierQuery,  # 接收 Schema 校验后的筛选条件。
    ) -> PageResult[SupplierOut]:  # 返回总条数、页码、页大小和当前页数据。
        """按 query 返回未删除供应商分页结果；应付余额批量汇总，避免逐条访问数据库。"""
        stmt = self.suppliers.build_query(query)  # 由 Repository 组合筛选与排序条件。
        rows, total = await self.suppliers.paginate(  # 复用通用分页查询。
            stmt,  # 已包含关键字、状态和软删除筛选的查询。
            query.page,  # 查询的页码，从 1 开始。
            query.page_size,  # 每页数量已由 Schema 限制范围。
        )  # rows 为当前页供应商对象，total 为符合筛选条件的总数。
        payable_map = await self.suppliers.sum_unpaid_payable(  # 汇总当前页供应商的未核销应付。
            [row.id for row in rows]  # 只查询当前页涉及的供应商，避免全表扫描。
        )  # 应付模块未上线时返回空映射，余额按 0 展示。
        items = [  # 将内部 ORM 对象转换为稳定的对外字段。
            SupplierOut.model_validate(row).model_copy(  # 从供应商列读取基本资料。
                update={  # payable_amount 不是供应商列，需要额外补入。
                    # 无应付记录时余额按 0 处理
                    "payable_amount": payable_map.get(row.id, Decimal("0"))
                }  # 不添加 Schema 之外的字段。
            )  # 每个供应商生成独立的出参模型。
            for row in rows  # 保持 Repository 给出的排序。
        ]  # 列表为空时仍返回合法分页结果。
        return paginate_result(items, total, query.page, query.page_size)  # 统一分页格式。

    async def get_supplier(self, supplier_id: int) -> SupplierOut:  # 查询单个供应商详情。
        """按 supplier_id 返回详情和应付余额；不存在或已软删除时抛出 404。"""
        supplier = await self._get_active_supplier(supplier_id)  # 停用但未删除的供应商仍可查看。
        return await self._to_out(supplier)  # 补齐应付余额后返回 Schema。

    async def list_selectable_suppliers(self) -> list[SupplierOut]:  # 供新单据选择供应商。
        """返回启用状态的供应商列表；停用供应商不会出现在新单据的可选列表中。

        历史单据按 supplier_id 关联原供应商，停用后历史单据仍能正常显示。
        """
        rows = await self.suppliers.list_selectable()  # 只取启用且未删除的供应商。
        payable_map = await self.suppliers.sum_unpaid_payable(  # 汇总这些供应商的应付余额。
            [row.id for row in rows]  # 只需汇总本次返回的供应商。
        )  # 应付模块未上线时返回空映射。
        return [  # 转成出参，便于前端直接渲染下拉选项。
            SupplierOut.model_validate(row).model_copy(  # 读取供应商基本资料。
                update={  # 补入应付余额。
                    "payable_amount": payable_map.get(row.id, Decimal("0"))  # 无应付记录时为 0。
                }  # 不添加 Schema 之外的字段。
            )  # 每个供应商生成独立的出参模型。
            for row in rows  # 保持 Repository 给出的编码排序。
        ]  # 列表为空时返回空数组。

    async def create_supplier(  # 新增供应商档案。
        self,  # 使用当前 Service 的仓储与事务。
        payload: SupplierCreate,  # 已通过基础校验的新增资料。
        current_user_id: int,  # 写入日志和档案审计字段的操作人。
    ) -> SupplierOut:  # 成功后返回新供应商详情。
        """用 payload 新建供应商并记录 current_user_id；返回详情，编码重复抛 409。

        名称全为空白时拒绝保存；任一步骤失败都会回滚资料和日志。
        """
        try:  # 从校验开始接管事务，避免失败后留下半条档案。
            await self.suppliers.lock_for_write()  # 取号与查重共用一把事务锁。
            code = (payload.code or "").strip() or await self._next_code()  # 空编码自动生成。
            name = payload.name.strip()  # 去掉名称首尾空白，避免保存肉眼不可见的名称。
            if not name:  # min_length 无法拒绝全为空格的字符串，业务层补充检查。
                raise AppException(ErrorCode.VALIDATION_ERROR, "供应商名称不能为空")  # 返回 422。
            await self._validate_unique(code)  # 已删除供应商占用的编码也不能再次使用。
            self._validate_credit_days(payload.credit_days)  # 账期不能为负，负数返回 422。
            supplier = BdSupplier(  # 经过校验后构造供应商模型。
                code=code,  # 已完成去空白或自动生成及唯一性检查。
                name=name,  # 保存清理后的必填名称。
                contact=payload.contact,  # 联系人允许未填写。
                phone=payload.phone,  # 联系电话允许未填写。
                address=payload.address,  # 地址允许未填写。
                tax_no=payload.tax_no,  # 税号允许未填写。
                bank=payload.bank,  # 开户行允许未填写。
                account=payload.account,  # 银行账号允许未填写。
                credit_days=payload.credit_days,  # 账期天数，用于推算应付到期日。
                status=payload.status,  # 1 表示启用，0 表示停用。
                remark=payload.remark,  # 备注仅保存，不擅自扩充出参字段。
                created_by=current_user_id,  # 记录谁创建了这份档案。
                updated_by=current_user_id,  # 初次保存的更新人同创建人。
            )  # 尚未提交，供应商和日志仍属于同一次业务操作。
            await self.suppliers.add(supplier)  # flush 并刷新主键，不在仓储里 commit。
            await self._write_log(current_user_id, "新增供应商", supplier.id)  # 记录新增操作。
            result = await self._to_out(supplier)  # 提交前完成出参校验，失败仍可整体回滚。
            await self.db.commit()  # 供应商和操作日志一次性生效。
            return result  # 返回已准备好的 Schema，不在提交后追加数据库查询。
        except Exception:  # 业务异常和数据库异常都必须撤销本次未完成操作。
            await self.db.rollback()  # 恢复本事务中已经 flush 的资料和日志。
            raise  # 保留原异常，交给统一异常处理器生成响应。

    async def update_supplier(  # 按实际传入字段编辑，停用也统一走这个入口。
        self,  # 复用当前 Service 的会话和仓储。
        supplier_id: int,  # 本次要编辑的供应商主键。
        payload: SupplierUpdate,  # 调用方实际提交的更新字段。
        current_user_id: int,  # 操作日志及更新人字段使用此 ID。
    ) -> SupplierOut:  # 返回更新后的供应商详情。
        """将 payload 的已传字段更新到 supplier_id，记录 current_user_id 后返回详情。

        不存在抛 404，必填列传空抛 422；编码重复抛 409；账期为负抛 422。
        可空列允许清空；停用只改变新单据的可选范围，不影响历史单据。
        """
        try:  # 更新和所有日志共用一次提交，失败则完整回滚。
            await self.suppliers.lock_for_write()  # 与新增、删除使用同一把事务锁。
            # 在检查软删除状态之前，读取最新供应商资料并加行锁。
            supplier = await self._get_active_supplier(supplier_id, for_update=True)
            data: dict[str, str | int | None] = payload.model_dump(  # 明确允许写入的值类型。
                exclude_unset=True  # 只保留实际传入字段，不能丢弃 False、0 或显式 None。
            )  # 未传字段保持数据库原值。
            for field in ("code", "name"):  # 这些列不允许数据库空值。
                if field in data and data[field] is None:  # 只拒绝显式 null，不误伤未传字段。
                    # 用 422 明确指出哪个必填字段被传成了 null。
                    raise AppException(ErrorCode.VALIDATION_ERROR, f"{field} 不能为 null")
            for field in ("code", "name"):  # 文本必填字段保存前去掉首尾空白。
                value = data.get(field)  # 没传的字段取不到字符串，不进行修改。
                if isinstance(value, str):  # 缩小类型后才调用字符串方法。
                    value = value.strip()  # 修改编码时同样按清理后的值查重。
                    if not value:  # 编码不能通过传空来重新取号，名称也不能只含空格。
                        # 清理后为空的必填字段返回 422，不能进入数据库。
                        raise AppException(ErrorCode.VALIDATION_ERROR, f"{field} 不能为空")
                    data[field] = value  # 只替换本次请求中的相应字段。
                    if field == "code":  # 名称不要求唯一，只有编码需要查重。
                        # 编辑时排除自己，保持原编码不应被判定为重复。
                        await self._validate_unique(value, exclude_id=supplier.id)
            if "credit_days" in data:  # 未修改账期时，不做重复校验。
                self._validate_credit_days(data["credit_days"])  # 账期为负时抛 422。
            action = "修改供应商"  # 普通字段修改使用统一动作名称。
            # 只有实际改变启用状态时，才把动作名称改为启用或停用。
            if "status" in data and data["status"] != supplier.status:
                # 区分启用与停用操作
                action = "启用供应商" if data["status"] == 1 else "停用供应商"
            for field, value in data.items():  # 只更新 Schema 允许且调用方实际传入的字段。
                setattr(supplier, field, value)  # None 清空可空列，False 和 0 也会正常保存。
            supplier.updated_by = current_user_id  # 档案中同步记录本次操作人。
            await self._write_log(current_user_id, action, supplier.id)  # flush 资料和操作日志。
            result = await self._to_out(supplier)  # 在事务内回填最新应付余额并验证出参。
            await self.db.commit()  # 供应商字段和日志一起生效。
            return result  # 提交成功后直接返回已生成的详情。
        except Exception:  # 包括校验、日志和数据库异常，不留半完成的编辑。
            await self.db.rollback()  # 释放锁并撤销已经 flush 的所有本次变更。
            raise  # 不吞异常，继续交给统一异常处理器。

    async def delete_supplier(  # 软删除供应商，并记录操作人。
        self,  # 使用当前 Service 的会话。
        supplier_id: int,  # 本次需要软删除的供应商主键。
        current_user_id: int,  # 执行删除的登录用户 ID。
    ) -> None:  # 删除成功不返回详情，历史资料仍在数据库中保留。
        """删除 supplier_id 并记录 current_user_id，无返回值。

        供应商不存在抛 404；存在未结采购订单、采购入库单或应付记录时抛 409。
        """
        try:  # 删除标记和日志必须一起提交或一起撤销。
            await self.suppliers.lock_for_write()  # 防止检查过程中被其他操作改写引用状态。
            # 取得最新供应商状态并加行锁，防止检查到会话里的旧数据。
            supplier = await self._get_active_supplier(supplier_id, for_update=True)
            await self._assert_can_delete(supplier)  # 有单据引用时拒绝删除。
            supplier.is_deleted = True  # 不调用 Repository.delete，避免物理删除历史档案。
            supplier.updated_by = current_user_id  # 记录执行软删除的操作人。
            # 日志仓储只 flush，软删除标记和日志仍未提交。
            await self._write_log(current_user_id, "删除供应商", supplier.id)
            await self.db.commit()  # 最后由 Service 提交，Repository 不拥有业务事务。
        except Exception:  # 删除或日志失败时恢复原供应商状态。
            await self.db.rollback()  # 撤销软删除标记及尚未成功的日志。
            raise  # 将原始异常交给统一错误处理。

    async def calculate_due_date(  # 按供应商账期推算应付到期日。
        self,  # 使用当前供应商查询能力。
        supplier_id: int,  # 应付单据对应的供应商 ID。
        bill_date: date,  # 单据日期，如入库日期或开票日期。
    ) -> date:  # 返回到期日 = 单据日期 + 账期天数。
        """按供应商账期计算应付到期日，供应付单据生成时调用。

        账期为 0 时到期日与单据日期相同；停用供应商同样可以计算历史单据的到期日。
        """
        supplier = await self._get_active_supplier(supplier_id)  # 读取账期配置。
        return bill_date + timedelta(days=supplier.credit_days)  # 按账期天数顺延。

    async def _get_active_supplier(  # 查询可访问的供应商，不能把停用与软删除混为一谈。
        self,  # 使用当前供应商 Repository。
        supplier_id: int,  # 要读取的供应商主键。
        *,  # 后面的开关必须写参数名，避免调用含义不清楚。
        for_update: bool = False,  # 写操作明确设为 True，读取时默认不加锁。
    ) -> BdSupplier:  # 返回供业务内部使用的供应商对象。
        """按 supplier_id 查未删除供应商；for_update 指定是否锁行，不存在或已删除抛出 404。"""
        if for_update:  # 写入口先取得全局供应商锁，再锁住目标行，保持加锁顺序一致。
            supplier = await self.suppliers.get_for_update(supplier_id)  # 强制读取最新资料。
        else:  # 详情查询不必阻塞其他写操作。
            supplier = await self.suppliers.get(supplier_id)  # 复用父类主键查询。
        if supplier is None or supplier.is_deleted:  # 父类 get 不过滤软删除，需要在这里判断。
            raise AppException(ErrorCode.NOT_FOUND, "供应商不存在")  # 外部统一收到 404。
        return supplier  # 停用但未删除的供应商仍允许查看、编辑或重新启用。

    async def _validate_unique(  # 检查清理后的编码是否被占用。
        self,  # 使用当前事务内的供应商查询。
        code: str,  # 待保存且已清理首尾空白的编码。
        exclude_id: int | None = None,  # 编辑时排除自己，新增时检查所有供应商。
    ) -> None:  # 校验失败抛异常，成功不返回数据。
        """校验 code 未被其他供应商占用，exclude_id 可排除自己；重复抛 409，成功无返回值。"""
        if await self.suppliers.get_by_code(code, exclude_id):  # 与数据库全表唯一约束保持一致。
            # 软删除记录仍占用唯一编码，与真实数据库约束一致。
            raise AppException(ErrorCode.CONFLICT, f"供应商编码已存在: {code}")

    @staticmethod
    def _validate_credit_days(credit_days: int | None) -> None:  # 校验账期天数。
        """账期为负数时抛 422；None 表示本次不修改账期，同样放行。"""
        if credit_days is None:  # 未传账期时保持原值。
            return  # 没有新值就不需要校验。
        if credit_days < 0:  # 负账期无法推算到期日，属于业务非法输入。
            raise AppException(ErrorCode.VALIDATION_ERROR, "账期天数不能为负数")

    async def _assert_can_delete(  # 删除前检查单据引用。
        self,  # 共用供应商引用查询能力。
        supplier: BdSupplier,  # 已锁定且确认未删除的目标供应商。
    ) -> None:  # 有冲突就抛出异常，允许删除时直接返回。
        """检查采购订单、入库单和应付记录；存在引用时抛 409，通过则无返回值。"""
        if await self.suppliers.has_open_purchase_order(supplier.id):
            # 未结采购订单说明业务仍在进行，供应商不能删除。
            raise AppException(ErrorCode.CONFLICT, "该供应商存在未结采购订单，无法删除")
        if await self.suppliers.has_inbound(supplier.id):
            # 已有入库单说明历史货物由其供货，档案需保留以便追溯。
            raise AppException(ErrorCode.CONFLICT, "该供应商存在采购入库单，无法删除")
        if await self.suppliers.has_payable(supplier.id):
            # 存在应付记录说明双方已有账务往来，删除会影响对账。
            raise AppException(ErrorCode.CONFLICT, "该供应商存在应付记录，无法删除")

    async def _next_code(self) -> str:  # 自动生成未填写的供应商编码。
        """在供应商写锁内读取最大序号，并用公共编码工具返回下一个 SUP 编码。"""
        max_code = await self.suppliers.max_code(SUPPLIER_CODE_PREFIX)  # 已软删的编码也纳入取号。
        return format_code(  # 不在业务层自行拼接编号字符串。
            SUPPLIER_CODE_PREFIX,  # 供应商统一使用 SUP 前缀。
            next_seq_from_code(max_code, SUPPLIER_CODE_PREFIX),  # 未有编码时从 1 开始。
        )  # 编码写入前仍由 _validate_unique 做统一查重。

    async def _to_out(self, supplier: BdSupplier) -> SupplierOut:  # 回填单条供应商的应付余额。
        """把 supplier 转成出参，返回 SupplierOut；无应付记录时余额为 0。"""
        payable_map = await self.suppliers.sum_unpaid_payable([supplier.id])  # 只查这一个供应商。
        return SupplierOut.model_validate(supplier).model_copy(  # 只暴露出参声明的字段。
            update={  # 补入供应商表本身没有的应付余额。
                "payable_amount": payable_map.get(supplier.id, Decimal("0"))  # 无记录时余额为 0。
            }  # 不添加 Schema 之外的字段。
        )  # 返回完整的 SupplierOut，而非 ORM 对象。

    async def _write_log(  # 记录供应商操作日志。
        self,  # 使用当前 Service 的日志仓储，共享业务会话。
        operator_id: int,  # 实际执行操作的登录用户 ID。
        action: str,  # 新增、修改、停用、启用或删除。
        target_id: int,  # 本次操作的供应商 ID。
    ) -> None:  # 只写当前事务，不直接返回或提交日志。
        """记录 operator_id 对 target_id 的 action，无返回值；写入失败向外抛错以整体回滚。"""
        params: dict[str, int] = {"supplierId": target_id}  # JSON 参数使用 camelCase。
        await self.oper_logs.add(  # 只 flush，是否提交由调用它的写操作决定。
            SysOperLog(  # 与其它基础资料使用同一张 sys_oper_log 表。
                user_id=operator_id,  # 记录操作人，不使用其他字段代替。
                module=OPER_MODULE,  # 所属模块为基础资料。
                action=action,  # 操作类型用于日志查询和审计。
                params=json.dumps(params, ensure_ascii=False),  # 保留中文，字段名遵循 JSON 约定。
                result="success",  # 成功日志与业务一起提交，业务失败时这条日志也回滚。
                status=1,  # 沿用已有操作日志的成功状态约定。
            )  # 日志异常继续向外抛出，由写操作入口回滚整个业务。
        )  # 不在这里 commit，避免破坏调用方事务边界。
