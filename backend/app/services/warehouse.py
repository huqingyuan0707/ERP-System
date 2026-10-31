"""仓库业务逻辑：编码、负责人、默认仓、库存保护与操作日志。

本层统一提交或回滚事务，仓库资料、默认标记和日志必须一起成功或一起撤销。
停用通过部分更新 status=0 完成；删除只设置软删除标记，保留历史引用。
"""

import json  # 将操作目标及默认仓变更明细写成 JSON，便于审计。

from sqlalchemy.ext.asyncio import AsyncSession  # 所有仓储共用请求内的异步会话。

from app.core.exceptions import AppException, ErrorCode  # 复用统一业务异常和 HTTP 错误码。
from app.models.base_data import BdWarehouse  # 仓库 ORM 对象，仅在业务层内部使用。
from app.models.sys import SysOperLog  # 操作日志和仓库写入同一个数据库事务。
from app.repositories.oper_log import OperLogRepository  # 日志仓储只 flush，不单独提交。
from app.repositories.user import UserRepository  # 校验负责人是否存在且未被软删除。
from app.repositories.warehouse import WarehouseRepository  # 封装仓库、负责人和库存查询。
from app.schemas.common import PageResult  # 列表统一使用项目分页结构。
from app.schemas.warehouse import (  # 请求与返回均使用仓库专用 Schema。
    WarehouseCreate,  # 新增请求，允许省略编码。
    WarehouseOut,  # 返回给调用方的仓库数据，不直接暴露 ORM。
    WarehouseQuery,  # 列表的分页、关键字和状态条件。
    WarehouseUpdate,  # 部分更新请求，区分未传字段与显式传空。
)  # Schema 已负责长度和状态范围等基础输入校验。
from app.utils.code_gen import format_code, next_seq_from_code  # 使用统一编码工具。
from app.utils.pagination import paginate_result  # 封装当前页数据及总条数。


WAREHOUSE_CODE_PREFIX = "WH"  # 与已有 WH0001 仓库编码保持一致。
OPER_MODULE = "基础资料"  # sys_oper_log 中记录本业务所属模块。


class WarehouseService:  # 汇集仓库业务规则，接口层只负责调用。
    """提供仓库查询和写操作；默认仓禁止直接取消、停用或删除，须先切换到其他仓。"""

    def __init__(self, db: AsyncSession) -> None:  # 接收请求级会话，不创建额外事务。
        """用 db 初始化全部仓储；它们共享同一事务，无返回值。"""
        self.db = db  # 提交和回滚统一由本 Service 控制。
        self.warehouses = WarehouseRepository(db)  # 访问仓库及关联库存。
        self.users = UserRepository(db)  # 检查负责人档案。
        self.oper_logs = OperLogRepository(db)  # 写入与仓库变更绑定的审计记录。

    async def list_warehouses(  # 分页查询仓库并一次性补齐负责人名称。
        self,  # 使用当前仓库 Service。
        query: WarehouseQuery,  # 接收 Schema 校验后的筛选条件。
    ) -> PageResult[WarehouseOut]:  # 返回总条数、页码、页大小和当前页数据。
        """按 query 返回未删除仓库分页结果；负责人姓名批量查询，避免逐条访问数据库。"""
        stmt = self.warehouses.build_query(query)  # 由 Repository 组合筛选与排序条件。
        rows, total = await self.warehouses.paginate(  # 复用通用分页查询。
            stmt,  # 已包含关键字、状态和软删除筛选的查询。
            query.page,  # 查询的页码，从 1 开始。
            query.page_size,  # 每页数量已由 Schema 限制范围。
        )  # rows 为当前页仓库对象，total 为符合筛选条件的总数。
        manager_ids = list(  # 相同负责人只查询一次。
            {row.manager_id for row in rows if row.manager_id is not None}  # 跳过未指定负责人。
        )  # 空列表由 Repository 直接返回空映射。
        manager_names = await self.warehouses.list_manager_names(manager_ids)  # 批量取姓名。
        items = [  # 将内部 ORM 对象转换为稳定的对外字段。
            WarehouseOut.model_validate(row).model_copy(  # 从仓库列读取基本资料。
                update={  # manager_name 不是仓库列，需要额外补入。
                    "manager_name": manager_names.get(row.manager_id)  # 已删除用户查不到姓名。
                    if row.manager_id is not None  # 没有负责人的仓库不查映射。
                    else None  # 姓名为空或负责人已删除时返回 null。
                }  # 不添加 Schema 之外的字段。
            )  # 每个仓库生成独立的出参模型。
            for row in rows  # 保持 Repository 给出的排序。
        ]  # 列表为空时仍返回合法分页结果。
        return paginate_result(items, total, query.page, query.page_size)  # 统一分页格式。

    async def get_warehouse(self, warehouse_id: int) -> WarehouseOut:  # 查询单个仓库详情。
        """按 warehouse_id 返回详情和负责人姓名；不存在或已软删除时抛出 404。"""
        warehouse = await self._get_active_warehouse(warehouse_id)  # 停用但未删除的仓仍可查看。
        return await self._to_out(warehouse)  # 补齐负责人姓名后返回 Schema。

    async def create_warehouse(  # 新增仓库，并按请求原子切换默认仓。
        self,  # 使用当前 Service 的仓储与事务。
        payload: WarehouseCreate,  # 已通过基础校验的新增资料。
        current_user_id: int,  # 写入日志和档案审计字段的操作人。
    ) -> WarehouseOut:  # 成功后返回新仓库详情。
        """用 payload 新建仓库并记录 current_user_id；返回详情，编码重复或默认仓停用抛 409。

        负责人不存在或名称全为空白时拒绝保存；任一步骤失败都会回滚资料、默认标记和日志。
        """
        try:  # 从校验开始接管事务，避免失败后留下旧默认已取消的状态。
            await self.warehouses.lock_for_write()  # 取号、查重和默认切换共用一把事务锁。
            code = (payload.code or "").strip() or await self._next_code()  # 空编码自动生成。
            name = payload.name.strip()  # 去掉名称首尾空白，避免保存肉眼不可见的名称。
            if not name:  # min_length 无法拒绝全为空格的字符串，业务层补充检查。
                raise AppException(ErrorCode.VALIDATION_ERROR, "仓库名称不能为空")  # 返回 422。
            await self._validate_unique(code)  # 已删除仓库占用的编码也不能再次使用。
            await self._validate_manager(payload.manager_id)  # 未设置负责人时允许通过。
            if payload.is_default and payload.status == 0:  # 默认仓必须可以被新单据选用。
                raise AppException(ErrorCode.CONFLICT, "停用仓库不能设为默认仓")  # 返回 409。
            previous_default_ids: list[int] = []  # 记录被取消的默认仓，供日志追溯。
            if payload.is_default:  # 普通仓库创建不改变当前默认仓。
                previous_default_ids = await self.warehouses.clear_defaults(  # 先取消旧默认。
                    current_user_id  # 同时写入旧默认仓的更新人。
                )  # 这里只更新未提交，后续失败时会一起恢复。
            warehouse = BdWarehouse(  # 经过校验后构造仓库模型。
                code=code,  # 已完成去空白或自动生成及唯一性检查。
                name=name,  # 保存清理后的必填名称。
                manager_id=payload.manager_id,  # 可空的负责人外键。
                is_default=payload.is_default,  # 旧默认已在同一事务中取消。
                address=payload.address,  # 地址允许未填写。
                status=payload.status,  # 1 表示启用，0 表示停用。
                remark=payload.remark,  # 备注仅保存，不擅自扩充出参字段。
                created_by=current_user_id,  # 记录谁创建了这份档案。
                updated_by=current_user_id,  # 初次保存的更新人同创建人。
            )  # 尚未提交，仓库和日志仍属于同一次业务操作。
            await self.warehouses.add(warehouse)  # flush 并刷新主键，不在仓储里 commit。
            await self._write_log(current_user_id, "新增仓库", warehouse.id)  # 记录新增操作。
            if payload.is_default:  # 默认仓变更单独留痕，即使此前没有默认仓也记录。
                await self._write_log(  # 新旧默认仓 ID 一起记入结构化参数。
                    current_user_id,  # 本次默认切换的操作人。
                    "切换默认仓",  # 默认仓变化使用独立动作便于审计。
                    warehouse.id,  # 切换后成为默认的仓库 ID。
                    previous_default_ids,  # 本次取消的旧默认仓 ID 列表。
                )  # 日志失败时不会留下没有审计记录的默认仓切换。
            result = await self._to_out(warehouse)  # 提交前完成出参校验，失败仍可整体回滚。
            await self.db.commit()  # 仓库、旧默认标记及操作日志一次性生效。
            return result  # 返回已准备好的 Schema，不在提交后追加数据库查询。
        except Exception:  # 业务异常和数据库异常都必须撤销本次未完成操作。
            await self.db.rollback()  # 恢复本事务中已经 flush 的资料、默认标记和日志。
            raise  # 保留原异常，交给统一异常处理器生成响应。

    async def update_warehouse(  # 按实际传入字段编辑，停用也统一走这个入口。
        self,  # 复用当前 Service 的会话和仓储。
        warehouse_id: int,  # 本次要编辑的仓库主键。
        payload: WarehouseUpdate,  # 调用方实际提交的更新字段。
        current_user_id: int,  # 操作日志及更新人字段使用此 ID。
    ) -> WarehouseOut:  # 返回更新后的仓库详情。
        """将 payload 的已传字段更新到 warehouse_id，记录 current_user_id 后返回详情。

        不存在抛 404，必填列传空或负责人不存在抛 422；编码重复、库存或默认仓限制抛 409。
        可空列允许清空；默认仓须先切到其他仓，不能通过同时取消默认和停用绕过保护。
        """
        try:  # 更新和所有日志共用一次提交，失败则完整回滚。
            await self.warehouses.lock_for_write()  # 与新增、删除使用同一把事务锁。
            # 在检查默认标记、软删除状态之前，读取最新仓库资料并加行锁。
            warehouse = await self._get_active_warehouse(warehouse_id, for_update=True)
            data: dict[str, str | int | bool | None] = payload.model_dump(  # 明确允许写入的值类型。
                exclude_unset=True  # 只保留实际传入字段，不能丢弃 False、0 或显式 None。
            )  # 未传字段保持数据库原值。
            for field in ("code", "name", "is_default", "status"):  # 这些列不允许数据库空值。
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
                        await self._validate_unique(value, exclude_id=warehouse.id)
            if "manager_id" in data:  # 未修改负责人时，不因历史负责人被删除而阻止其他编辑。
                await self._validate_manager(payload.manager_id)  # 显式 None 表示清空负责人。
            if data.get("status") == 0:  # 0 是停用请求，不能按字段真假值判断。
                await self._assert_can_remove(warehouse, "停用")  # 先查库存，再保护当前默认仓。
            # 不允许先取消默认，再另发停用请求来绕过“先切换”的保护。
            if warehouse.is_default and data.get("is_default") is False:
                raise AppException(  # 提示用户通过另一仓库完成默认切换。
                    ErrorCode.CONFLICT,  # 取消默认标记违反默认仓保护规则，返回 409。
                    "不能直接取消默认仓，请先将其他仓库设为默认仓",  # 告知正确切换方式。
                )  # 此时尚未修改任何仓库字段。
            if (  # 检查更新后的完整状态，允许同时启用并设为默认。
                data.get("is_default", warehouse.is_default)  # 未传默认标记就沿用旧值。
                and data.get("status", warehouse.status) == 0  # 默认仓不能处于停用状态。
            ):  # 两个条件同时满足表示更新后的状态不合法。
                raise AppException(ErrorCode.CONFLICT, "停用仓库不能设为默认仓")  # 返回 409。
            was_default = warehouse.is_default  # 保存变更前状态，用于判断是否需要默认切换日志。
            previous_default_ids: list[int] = []  # 普通编辑不会涉及其他默认仓。
            if data.get("is_default") is True:  # 只有明确要求设为默认时才取消其他默认。
                previous_default_ids = await self.warehouses.clear_defaults(  # 同事务先取消旧默认。
                    current_user_id,  # 记录旧默认仓的更新人。
                    exclude_id=warehouse.id,  # 目标仓库的字段由本次部分更新写入。
                )  # 不依赖原默认存在，首次设置也能处理。
            action = "修改仓库"  # 普通字段修改使用统一动作名称。
            # 只有实际改变启用状态时，才把动作名称改为启用或停用。
            if "status" in data and data["status"] != warehouse.status:
                action = "启用仓库" if data["status"] == 1 else "停用仓库"  # 区分启用与停用操作。
            for field, value in data.items():  # 只更新 Schema 允许且调用方实际传入的字段。
                setattr(warehouse, field, value)  # None 清空可空列，False 和 0 也会正常保存。
            warehouse.updated_by = current_user_id  # 档案中同步记录本次操作人。
            await self._write_log(current_user_id, action, warehouse.id)  # flush 资料和操作日志。
            # 重复把同一仓设为默认无需伪造切换，首次设置或替换旧默认才额外记录。
            if data.get("is_default") is True and (not was_default or previous_default_ids):
                await self._write_log(  # 记录新旧默认仓，便于审计默认仓选择变化。
                    current_user_id,  # 本次默认切换的操作人。
                    "切换默认仓",  # 默认仓变化使用独立动作便于审计。
                    warehouse.id,  # 切换后成为默认的仓库 ID。
                    previous_default_ids,  # 本次取消的旧默认仓 ID 列表。
                )  # 与资料修改共享事务，失败时全部撤销。
            result = await self._to_out(warehouse)  # 在事务内回填最新负责人名称并验证出参。
            await self.db.commit()  # 仓库字段、其他仓默认标记和日志一起生效。
            return result  # 提交成功后直接返回已生成的详情。
        except Exception:  # 包括校验、日志和数据库异常，不留半完成的编辑。
            await self.db.rollback()  # 释放锁并撤销已经 flush 的所有本次变更。
            raise  # 不吞异常，继续交给统一异常处理器。

    async def delete_warehouse(  # 软删除仓库，并记录操作人。
        self,  # 使用当前 Service 的会话。
        warehouse_id: int,  # 本次需要软删除的仓库主键。
        current_user_id: int,  # 执行删除的登录用户 ID。
    ) -> None:  # 删除成功不返回详情，历史资料仍在数据库中保留。
        """删除 warehouse_id 并记录 current_user_id，无返回值。

        仓库不存在抛 404，仍有库存或仍为默认仓时抛 409。
        """
        try:  # 删除标记和日志必须一起提交或一起撤销。
            await self.warehouses.lock_for_write()  # 防止检查过程中被其他仓库操作切成默认仓。
            # 取得最新仓库状态并加行锁，防止检查到会话里的旧默认标记。
            warehouse = await self._get_active_warehouse(warehouse_id, for_update=True)
            await self._assert_can_remove(warehouse, "删除")  # 未清空库存或仍为默认仓时拒绝删除。
            warehouse.is_deleted = True  # 不调用 Repository.delete，避免物理删除历史档案。
            warehouse.updated_by = current_user_id  # 记录执行软删除的操作人。
            # 日志仓储只 flush，软删除标记和日志仍未提交。
            await self._write_log(current_user_id, "删除仓库", warehouse.id)
            await self.db.commit()  # 最后由 Service 提交，Repository 不拥有业务事务。
        except Exception:  # 删除或日志失败时恢复原仓库状态。
            await self.db.rollback()  # 撤销软删除标记及尚未成功的日志。
            raise  # 将原始异常交给统一错误处理。

    async def _get_active_warehouse(  # 查询可访问的仓库，不能把停用与软删除混为一谈。
        self,  # 使用当前仓库 Repository。
        warehouse_id: int,  # 要读取的仓库主键。
        *,  # 后面的开关必须写参数名，避免调用含义不清楚。
        for_update: bool = False,  # 写操作明确设为 True，读取时默认不加锁。
    ) -> BdWarehouse:  # 返回供业务内部使用的仓库对象。
        """按 warehouse_id 查未删除仓库；for_update 指定是否锁行，不存在或已删除抛出 404。"""
        if for_update:  # 写入口先取得全局仓库锁，再锁住目标行，保持加锁顺序一致。
            warehouse = await self.warehouses.get_for_update(warehouse_id)  # 强制读取最新资料。
        else:  # 详情查询不必阻塞其他写操作。
            warehouse = await self.warehouses.get(warehouse_id)  # 复用父类主键查询。
        if warehouse is None or warehouse.is_deleted:  # 父类 get 不过滤软删除，需要在这里判断。
            raise AppException(ErrorCode.NOT_FOUND, "仓库不存在")  # 外部统一收到 404。
        return warehouse  # 停用但未删除的仓库仍允许查看、编辑或重新启用。

    async def _validate_unique(  # 检查清理后的编码是否被占用。
        self,  # 使用当前事务内的仓库查询。
        code: str,  # 待保存且已清理首尾空白的编码。
        exclude_id: int | None = None,  # 编辑时排除自己，新增时检查所有仓库。
    ) -> None:  # 校验失败抛异常，成功不返回数据。
        """校验 code 未被其他仓占用，exclude_id 可排除自己；重复抛 409，成功无返回值。"""
        if await self.warehouses.get_by_code(code, exclude_id):  # 与数据库全表唯一约束保持一致。
            # 软删除记录仍占用唯一编码，与真实数据库约束一致。
            raise AppException(ErrorCode.CONFLICT, f"仓库编码已存在: {code}")

    async def _validate_manager(self, manager_id: int | None) -> None:  # 检查可选的负责人引用。
        """manager_id 可为 None；指定用户不存在或已软删除时抛 422，校验成功无返回值。"""
        if manager_id is None:  # 负责人可以未设置，也允许在编辑时主动清空。
            return  # 没有外键就不访问用户表。
        manager = await self.users.get(manager_id)  # 检查用户档案，而不是只检查姓名是否为空。
        if manager is None or manager.is_deleted:  # 有账号但没填姓名仍算有效负责人。
            raise AppException(ErrorCode.VALIDATION_ERROR, "仓库负责人不存在")  # 避免保存无效外键。

    async def _assert_can_remove(  # 停用和删除复用同一套库存及默认仓保护。
        self,  # 共用仓库库存查询能力。
        warehouse: BdWarehouse,  # 已锁定且确认未删除的目标仓库。
        action: str,  # 错误提示中的操作名称，使用停用或删除。
    ) -> None:  # 有冲突就抛出异常，允许操作时直接返回。
        """检查 warehouse 的库存和默认标记；不允许 action 时抛 409，通过则无返回值。"""
        # 按行检查 qty 和 locked_qty，正数、负数或锁定数量非零都不能当作空仓。
        if await self.warehouses.has_stock(warehouse.id):
            # 用 409 提示库存冲突，不把负库存或正负抵消误判为可删除。
            raise AppException(ErrorCode.CONFLICT, f"该仓库存在库存，无法{action}")
        if warehouse.is_default:  # 校验修改前默认状态，不能靠同一请求先取消默认绕过限制。
            raise AppException(  # 给出可执行的处理方式，不自动猜测替代仓库。
                ErrorCode.CONFLICT,  # 仍为默认仓时停用或删除属于业务冲突。
                f"默认仓库不能{action}，请先将其他仓库设为默认仓",  # 引导用户先切换。
            )  # 库存或默认检查失败时，调用入口会统一回滚事务。

    async def _next_code(self) -> str:  # 自动生成未填写的仓库编码。
        """在仓库写锁内读取最大序号，并用公共编码工具返回下一个 WH 编码。"""
        max_code = await self.warehouses.max_code(WAREHOUSE_CODE_PREFIX)  # 已软删的编码也纳入取号。
        return format_code(  # 不在业务层自行拼接编号字符串。
            WAREHOUSE_CODE_PREFIX,  # 仓库统一使用 WH 前缀。
            next_seq_from_code(max_code, WAREHOUSE_CODE_PREFIX),  # 未有编码时从 1 开始。
        )  # 编码写入前仍由 _validate_unique 做统一查重。

    async def _to_out(self, warehouse: BdWarehouse) -> WarehouseOut:  # 回填单条仓库的负责人名称。
        """把 warehouse 转成出参，返回 WarehouseOut；无负责人或负责人已删除时姓名为 None。"""
        # 仅查此仓库的负责人，没有负责人时用空列表跳过查询。
        manager_ids = [] if warehouse.manager_id is None else [warehouse.manager_id]
        names = await self.warehouses.list_manager_names(manager_ids)  # 复用批量姓名查询。
        # 负责人未设置或已删除时姓名为 None；未填姓名的有效用户也保留 None。
        manager_name = names.get(warehouse.manager_id) if warehouse.manager_id is not None else None
        return WarehouseOut.model_validate(warehouse).model_copy(  # 只暴露出参声明的字段。
            update={"manager_name": manager_name}  # 补入仓库表本身没有的负责人姓名。
        )  # 返回完整的 WarehouseOut，而非 ORM 对象。

    async def _write_log(  # 按用户管理的日志写法，记录仓库目标及默认变更详情。
        self,  # 使用当前 Service 的日志仓储，共享业务会话。
        operator_id: int,  # 实际执行操作的登录用户 ID。
        action: str,  # 新增、修改、停用、启用、删除或切换默认仓。
        target_id: int,  # 操作的仓库 ID，默认切换时表示新默认仓。
        previous_default_ids: list[int] | None = None,  # 仅默认切换日志携带旧默认仓列表。
    ) -> None:  # 只写当前事务，不直接返回或提交日志。
        """记录 operator_id 对 target_id 的 action，无返回值；写入失败向外抛错以整体回滚。

        previous_default_ids 为 None 表示普通操作，为列表表示默认切换，空列表表示首次设默认。
        """
        params: dict[str, int | list[int]] = {"warehouseId": target_id}  # JSON 参数使用 camelCase。
        if previous_default_ids is not None:  # 空列表也要保留，区别于没有默认仓变更。
            params["previousDefaultWarehouseIds"] = previous_default_ids  # 明确哪些旧默认仓被取消。
            params["defaultWarehouseId"] = target_id  # 明确这次设成默认的目标仓库。
        await self.oper_logs.add(  # 只 flush，是否提交由调用它的写操作决定。
            SysOperLog(  # 与用户管理使用同一张 sys_oper_log 表。
                user_id=operator_id,  # 记录操作人，不使用负责人 ID 代替。
                module=OPER_MODULE,  # 所属模块为基础资料。
                action=action,  # 操作类型用于日志查询和审计。
                params=json.dumps(params, ensure_ascii=False),  # 保留中文，字段名遵循 JSON 约定。
                result="success",  # 成功日志与业务一起提交，业务失败时这条日志也回滚。
                status=1,  # 沿用已有操作日志的成功状态约定。
            )  # 日志异常继续向外抛出，由写操作入口回滚整个业务。
        )  # 不在这里 commit，避免破坏调用方事务边界。
