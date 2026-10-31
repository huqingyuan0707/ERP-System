"""供应商数据访问：封装查询、编码取号与单据引用检查，业务判断与事务提交由 Service 负责。"""

from decimal import Decimal  # 定点数类型，应付余额汇总用金额类型避免浮点误差。

from sqlalchemy import (  # 查询类型、绑定参数与读写语句。
    Select,
    bindparam,
    func,
    or_,
    select,
    text,
)

from app.models.base_data import BdSupplier  # 供应商表 bd_supplier 的 ORM 模型。
from app.repositories.base import BaseRepository  # 复用数据库会话与分页等通用操作。
from app.schemas.supplier import SupplierQuery  # 供应商列表的分页、关键字和状态参数。


# 采购订单中表示"已结清"的状态；其余状态都属于未结采购订单，供应商不可删除。
CLOSED_ORDER_STATUSES = ("完成", "关闭")

# 引用检查涉及的外部表；这些表由采购、财务模块创建，尚未创建时跳过对应检查。
PURCHASE_ORDER_TABLE = "pur_order"  # 采购订单表，判断是否存在未结采购订单。
PURCHASE_INBOUND_TABLE = "pur_inbound"  # 采购入库表，判断是否已有历史入库单。
PAYABLE_TABLE = "fin_payable"  # 应付单据表，判断是否存在应付记录并汇总未核销金额。


class SupplierRepository(BaseRepository[BdSupplier]):  # 将通用仓储绑定到供应商模型。
    """封装供应商及外部单据的读写，不直接提交事务或抛出业务异常。"""

    model = BdSupplier  # 父类 get、add、paginate 等通用操作使用此模型。

    async def lock_for_write(self) -> None:  # 将供应商写操作串行化，防止并发生成重复编码。
        """取得当前事务的供应商写锁，无返回值；提交或回滚时由数据库自动释放。

        锁定固定的表名哈希，即使还没有供应商，也不会发生空集合无法加锁的问题。
        SQLite 仅用于隔离测试，不执行 PostgreSQL 专用锁，不代表验证了生产库并发行为。
        """
        if self.db.get_bind().dialect.name == "postgresql":  # 生产库使用事务级 advisory 锁。
            await self.db.execute(  # 锁与当前会话事务绑定，仓储不自行开启或提交事务。
                select(func.pg_advisory_xact_lock(func.hashtext("bd_supplier")))  # 使用固定锁名。
            )  # 同名锁的其他写事务会等待本次业务提交或回滚。

    async def get_for_update(self, supplier_id: int) -> BdSupplier | None:  # 读取并锁住目标供应商。
        """按 supplier_id 返回最新供应商或 None，并持有行锁至事务结束；包含软删除记录。"""
        stmt = (  # 修改前重新取值，避免同一会话中的旧对象影响删除状态判断。
            select(BdSupplier)  # 返回完整供应商对象，供 Service 判断并修改。
            .where(BdSupplier.id == supplier_id)  # 只锁定本次操作的目标供应商。
            .with_for_update()  # 行锁随业务事务一起释放。
            .execution_options(populate_existing=True)  # 覆盖会话中可能缓存的旧字段值。
        )  # 业务层仍需判断记录是否已经软删除。
        return (await self.db.execute(stmt)).scalar_one_or_none()  # 不存在时返回 None。

    async def max_code(self, prefix: str) -> str | None:  # 为统一编码工具查询最大的已用序号。
        """返回 prefix 下数字序号最大的编码或 None；含软删除记录，忽略非数字自定义编码。"""
        stmt = select(BdSupplier.code).where(BdSupplier.code.like(f"{prefix}%"))  # 先筛前缀。
        codes = (await self.db.execute(stmt)).scalars().all()  # 已删除供应商的编码也不能重复使用。
        numeric_codes = [  # 只让标准数字后缀参与自动取号，不受 SUP-01 等自定义编码影响。
            code  # 保留完整编码，交给统一工具解析下一个序号。
            for code in codes  # 检查数据库返回的同前缀编码。
            # 只接受 ASCII 数字后缀，保证后续 int 转换与编码格式一致。
            if (suffix := code.removeprefix(prefix)).isascii() and suffix.isdecimal()
        ]  # 空后缀及非数字后缀不会被转成整数。
        return max(  # 比较数字大小，避免 SUP9999 在字符串排序中压过 SUP10000。
            numeric_codes,  # 只比较合法的数字编码。
            key=lambda code: int(code[len(prefix) :]),  # 按序号大小而非字符串排序。
            default=None,  # 还没有数字编码时返回 None。
        )  # 这里只读取数据，不预占序号或提交事务。

    async def get_by_code(  # 新增或编辑时查询是否已有记录占用编码。
        self,  # 使用当前供应商查询会话。
        code: str,  # 待检查是否被占用的编码。
        exclude_id: int | None = None,  # 编辑时传当前供应商 ID，避免查重命中自身。
    ) -> BdSupplier | None:  # 返回占用编码的对象，未找到则返回 None。
        """按 code 查重；exclude_id 可排除当前供应商，便于编辑时保持原编码。

        编码使用全表唯一索引，因此软删除记录也参与查重，避免入库时才报冲突。
        本方法只返回供应商或 None，是否允许保存由 Service 判断。
        """
        stmt = select(BdSupplier).where(BdSupplier.code == code)  # 包含软删除记录。
        if exclude_id is not None:  # 没传排除 ID 时，检查全部记录。
            stmt = stmt.where(BdSupplier.id != exclude_id)  # 避免把自己当作重复记录。
        return (await self.db.execute(stmt)).scalar_one_or_none()  # 返回 ORM 对象而非 Row。

    def build_query(  # 只构造 SQL，不访问数据库，因此无需 async。
        self,  # 使用当前供应商模型构建查询。
        query: SupplierQuery,  # 接收已通过 Schema 校验的查询参数。
    ) -> Select[tuple[BdSupplier]]:  # 查询每行只包含一个供应商 ORM 对象。
        """根据 keyword、status 构造未删除供应商查询；返回 SQL，交给父类分页执行。"""
        stmt = select(BdSupplier).where(BdSupplier.is_deleted.is_(False))  # 排除软删除。
        if query.keyword:  # 未提供关键字或为空字符串时，不追加模糊查询。
            like = f"%{query.keyword}%"  # 百分号表示任意字符，由 ORM 绑定参数。
            stmt = stmt.where(  # 追加关键字条件，与其他筛选条件同时满足。
                or_(  # 编码、名称、联系人、电话任一匹配即可。
                    BdSupplier.code.ilike(like),  # 编码包含关键字，不区分大小写。
                    BdSupplier.name.ilike(like),  # 名称使用相同的模糊匹配方式。
                    BdSupplier.contact.ilike(like),  # 联系人也可用于查找供应商。
                    BdSupplier.phone.ilike(like),  # 电话同样参与关键字匹配。
                )  # 结束多字段的或条件组合。
            )  # 将关键字条件附加到查询语句。
        if query.status is not None:  # 0 表示停用，不能用 if query.status 判断。
            stmt = stmt.where(BdSupplier.status == query.status)  # 精确匹配状态。
        return stmt.order_by(BdSupplier.id.desc())  # ID 倒序；分页复用父类 paginate。

    async def list_selectable(self) -> list[BdSupplier]:  # 供采购订单等单据选择供应商。
        """返回启用状态的供应商；停用供应商不会出现在新单据的可选列表中。

        历史单据仍按 supplier_id 关联原供应商，停用不影响既有单据的查询与展示。
        """
        stmt = (  # 只返回未删除且处于启用状态的供应商。
            select(BdSupplier)  # 返回完整供应商对象，供单据回填名称。
            .where(BdSupplier.is_deleted.is_(False))  # 排除软删除记录。
            .where(BdSupplier.status == 1)  # 停用的供应商不能被新单据选中。
            .order_by(BdSupplier.code.asc())  # 按编码排序，便于下拉框稳定排列。
        )  # 列表直接返回 ORM 对象，由 Service 转成出参。
        return list((await self.db.execute(stmt)).scalars().all())  # 返回全部可选供应商。

    async def existing_tables(self) -> set[str]:  # 判断外部单据表是否已经建好。
        """返回当前数据库实际存在的表名集合。

        采购、财务模块尚未落地时相关表不存在，引用检查需要跳过这些表，
        对应模块上线后同一段检查逻辑即可自动生效，无需改写业务代码。
        """
        dialect = self.db.get_bind().dialect.name  # 识别数据库方言以选择系统目录查询。
        if dialect == "postgresql":  # PostgreSQL 从 information_schema 读取表名。
            sql = (
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema()"
            )
        elif dialect == "sqlite":  # SQLite 从 sqlite_master 读取表名。
            sql = "SELECT name FROM sqlite_master WHERE type = 'table'"
        else:  # 其它方言没有统一的目录查询方式，跳过外部单据检查。
            return set()  # 返回空集合，表示没有可检查的外部表。
        rows = (await self.db.execute(text(sql))).scalars().all()  # 读取全部表名。
        return {str(name) for name in rows}  # 统一转成字符串集合便于比较。

    async def has_open_purchase_order(self, supplier_id: int) -> bool:  # 判断是否存在未结采购订单。
        """返回该供应商是否存在未结清的采购订单；采购订单表未创建时返回 False。

        已完成和已关闭的订单视为结清，不再阻止供应商删除；草稿、待审、
        已审和部分入库的订单都算未结，供应商仍被业务单据引用。
        """
        if PURCHASE_ORDER_TABLE not in await self.existing_tables():  # 采购模块尚未上线。
            return False  # 没有订单表就没有引用，跳过检查。
        # 状态值来自模块常量，不接受用户输入，拼接不会造成注入风险
        closed = ", ".join(f"'{status}'" for status in CLOSED_ORDER_STATUSES)
        stmt = text(  # 直接按表名查询，订单表结构由采购模块维护。
            f"SELECT 1 FROM {PURCHASE_ORDER_TABLE} "  # 表名来自模块常量，不接受外部输入。
            "WHERE supplier_id = :supplier_id "  # 限定当前供应商。
            "AND is_deleted IS NOT TRUE "  # 排除已软删除的订单。
            f"AND status NOT IN ({closed}) LIMIT 1"  # 只看未结清的订单。
        )  # EXISTS 语义：查到一行即说明存在未结订单。
        result = await self.db.execute(stmt, {"supplier_id": supplier_id})  # 参数绑定避免注入。
        return result.first() is not None  # 有记录返回 True。

    async def has_inbound(self, supplier_id: int) -> bool:  # 判断是否已有历史入库单。
        """返回该供应商是否存在采购入库单；入库表未创建时返回 False。

        已有入库单说明历史货物已由该供应商供货，属于真实业务引用，
        因此不允许删除供应商档案，只能停用。
        """
        if PURCHASE_INBOUND_TABLE not in await self.existing_tables():  # 采购模块尚未上线。
            return False  # 没有入库表就没有引用，跳过检查。
        stmt = text(  # 按供应商查询入库单是否已存在。
            f"SELECT 1 FROM {PURCHASE_INBOUND_TABLE} "  # 表名来自模块常量，不接受外部输入。
            "WHERE supplier_id = :supplier_id "  # 限定当前供应商。
            "AND is_deleted IS NOT TRUE LIMIT 1"  # 排除已软删除的入库单。
        )  # EXISTS 语义：查到一行即说明存在历史入库。
        result = await self.db.execute(stmt, {"supplier_id": supplier_id})  # 参数绑定避免注入。
        return result.first() is not None  # 有记录返回 True。

    async def has_payable(self, supplier_id: int) -> bool:  # 判断是否存在应付记录。
        """返回该供应商是否存在应付单据；应付表未创建时返回 False。

        只要存在应付记录就说明双方已有账务往来，即使已全额核销，
        也应保留档案以备对账，因此同样阻止删除。
        """
        if PAYABLE_TABLE not in await self.existing_tables():  # 财务模块尚未上线。
            return False  # 没有应付表就没有引用，跳过检查。
        stmt = text(  # 按供应商查询应付单据是否已存在。
            f"SELECT 1 FROM {PAYABLE_TABLE} "  # 表名来自模块常量，不接受外部输入。
            "WHERE supplier_id = :supplier_id "  # 限定当前供应商。
            "AND is_deleted IS NOT TRUE LIMIT 1"  # 排除已软删除的应付单。
        )  # EXISTS 语义：查到一行即说明存在应付。
        result = await self.db.execute(stmt, {"supplier_id": supplier_id})  # 参数绑定避免注入。
        return result.first() is not None  # 有记录返回 True。

    async def sum_unpaid_payable(  # 汇总各供应商的未核销应付金额。
        self,  # 使用当前事务会话查询应付表。
        supplier_ids: list[int],  # 由 Service 收集当前页供应商 ID。
    ) -> dict[int, Decimal]:  # 返回供应商 ID 到未核销金额的映射。
        """按 supplier_ids 汇总未核销应付金额；应付表未创建时返回空映射。

        汇总的是 unpaid_amount（未核销金额），已付款部分不计入应付余额；
        调用方对缺失的供应商按 0 处理即可。
        """
        if not supplier_ids:  # 当前页没有供应商时，不必访问数据库。
            return {}  # 空映射可直接交给 Service 做余额回填。
        if PAYABLE_TABLE not in await self.existing_tables():  # 财务模块尚未上线。
            return {}  # 应付表不存在，所有供应商余额按 0 显示。
        stmt = (  # 按供应商分组汇总未核销金额。
            text(  # 直接按表名查询，应付表结构由财务模块维护。
                # 表名来自模块常量，不接受外部输入
                f"SELECT supplier_id, SUM(COALESCE(unpaid_amount, 0)) FROM {PAYABLE_TABLE} "
                "WHERE supplier_id IN :supplier_ids "  # 只统计当前页涉及的供应商。
                "AND is_deleted IS NOT TRUE "  # 排除已软删除的应付单。
                "GROUP BY supplier_id"  # 每个供应商一行汇总结果。
            ).bindparams(bindparam("supplier_ids", expanding=True))  # IN 列表使用展开参数绑定。
        )  # 展开参数由 SQLAlchemy 生成占位符，不做字符串拼接。
        rows = (await self.db.execute(stmt, {"supplier_ids": supplier_ids})).all()  # 读取汇总结果。
        return {  # 转成 ID 到金额的字典，便于按供应商回填。
            int(row[0]): Decimal(str(row[1] or 0))  # 数据库可能返回 float，统一转 Decimal。
            for row in rows  # 逐行处理汇总结果。
        }  # 没有应付记录的供应商不会出现在结果中。
