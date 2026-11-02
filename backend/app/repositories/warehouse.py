"""仓库数据访问：封装查询、锁和默认标记更新，业务判断与事务提交由 Service 负责。"""

from sqlalchemy import Select, func, or_, select, update  # 查询类型、函数、条件与读写语句。

from app.models.base_data import BdWarehouse  # 仓库表 bd_warehouse 的 ORM 模型。
from app.models.inventory import InvStock  # 库存表 inv_stock，供停用与删除前检查。
from app.models.sys import SysUser  # 用户表 sys_user，提供负责人的真实姓名。
from app.repositories.base import BaseRepository  # 复用数据库会话与分页等通用操作。
from app.schemas.warehouse import WarehouseQuery  # 仓库列表的分页、关键字和状态参数。


class WarehouseRepository(BaseRepository[BdWarehouse]):  # 将通用仓储绑定到仓库模型。
    """封装仓库及关联资料的读写与锁，不直接提交事务或抛出库存冲突等业务异常。"""

    model = BdWarehouse  # 父类 get、add、paginate 等通用操作使用此模型。

    async def lock_for_write(self) -> None:  # 将仓库写操作串行化，防止并发生成重复默认仓。
        """取得当前事务的仓库写锁，无返回值；提交或回滚时由 PostgreSQL 自动释放。

        锁定固定的表名哈希，即使还没有仓库或默认仓，也不会发生空集合无法加锁的问题。
        SQLite 仅用于隔离测试，不执行 PostgreSQL 专用锁，不代表验证了生产库并发行为。
        """
        if self.db.get_bind().dialect.name == "postgresql":  # 生产库使用事务级 advisory 锁。
            await self.db.execute(  # 锁与当前会话事务绑定，仓储不自行开启或提交事务。
                select(func.pg_advisory_xact_lock(func.hashtext("bd_warehouse")))  # 使用固定锁名。
            )  # 同名锁的其他写事务会等待本次业务提交或回滚。

    async def get_for_update(self, warehouse_id: int) -> BdWarehouse | None:  # 读取并锁住目标仓。
        """按 warehouse_id 返回最新仓库或 None，并持有行锁至事务结束；包含软删除记录。"""
        stmt = (  # 修改前重新取值，避免同一会话中的旧对象影响默认仓和删除状态判断。
            select(BdWarehouse)  # 返回完整仓库对象，供 Service 判断并修改。
            .where(BdWarehouse.id == warehouse_id)  # 只锁定本次操作的目标仓库。
            .where(BdWarehouse.tenant_id == self.tenant_id)  # 租户隔离：只锁定当前租户数据。
            .with_for_update()  # PostgreSQL 行锁随业务事务一起释放。
            .execution_options(populate_existing=True)  # 覆盖会话中可能缓存的旧字段值。
        )  # 业务层仍需判断记录是否已经软删除。
        return (await self.db.execute(stmt)).scalar_one_or_none()  # 不存在时返回 None。

    async def max_code(self, prefix: str) -> str | None:  # 为统一编码工具查询最大的已用序号。
        """返回 prefix 下数字序号最大的编码或 None；含软删除记录，忽略非数字自定义编码。"""
        stmt = select(BdWarehouse.code).where(  # 先筛前缀，再筛当前租户。
            BdWarehouse.code.like(f"{prefix}%"),  # 编码前缀匹配。
            BdWarehouse.tenant_id == self.tenant_id,  # 租户隔离，避免跨租户取号冲突。
        )
        codes = (await self.db.execute(stmt)).scalars().all()  # 已删除仓的编码也不能重复使用。
        numeric_codes = [  # 只让标准数字后缀参与自动取号，不受 WH-MAIN 等自定义编码影响。
            code  # 保留完整编码，交给统一工具解析下一个序号。
            for code in codes  # 检查数据库返回的同前缀编码。
            # 只接受 ASCII 数字后缀，保证后续 int 转换与编码格式一致。
            if (suffix := code.removeprefix(prefix)).isascii() and suffix.isdecimal()
        ]  # 空后缀及非数字后缀不会被转成整数。
        return max(  # 比较数字大小，避免 WH9999 在字符串排序中压过 WH10000。
            numeric_codes,  # 只比较合法的数字编码。
            key=lambda code: int(code[len(prefix) :]),  # 按序号大小而非字符串排序。
            default=None,  # 还没有数字编码时返回 None。
        )  # 这里只读取数据，不预占序号或提交事务。

    async def get_by_code(  # 新增或编辑时查询是否已有记录占用编码。
        self,  # 使用当前仓库查询会话。
        code: str,  # 待检查是否被占用的编码。
        exclude_id: int | None = None,  # 编辑时传当前仓库 ID，避免查重命中自身。
    ) -> BdWarehouse | None:  # 返回占用编码的对象，未找到则返回 None。
        """按 code 查重；exclude_id 可排除当前仓库，便于编辑时保持原编码。

        编码使用全表唯一索引，因此软删除记录也参与查重，避免入库时才报冲突。
        本方法只返回仓库或 None，是否允许保存由 Service 判断。
        """
        stmt = select(BdWarehouse).where(  # 包含软删除记录，并按租户隔离。
            BdWarehouse.code == code,
            BdWarehouse.tenant_id == self.tenant_id,  # 租户隔离，避免跨租户编码冲突。
        )
        if exclude_id is not None:  # 没传排除 ID 时，检查全部记录。
            stmt = stmt.where(BdWarehouse.id != exclude_id)  # 避免把自己当作重复记录。
        return (await self.db.execute(stmt)).scalar_one_or_none()  # 返回 ORM 对象而非 Row。

    def build_query(  # 只构造 SQL，不访问数据库，因此无需 async。
        self,  # 使用当前仓库模型构建查询。
        query: WarehouseQuery,  # 接收已通过 Schema 校验的查询参数。
    ) -> Select[tuple[BdWarehouse]]:  # 查询每行只包含一个仓库 ORM 对象。
        """根据 keyword、status 构造未删除仓库查询；返回 SQL，交给父类分页执行。"""
        stmt = select(BdWarehouse).where(  # 排除软删除，并按租户隔离。
            BdWarehouse.is_deleted.is_(False),
            BdWarehouse.tenant_id == self.tenant_id,  # 租户隔离，只查当前租户仓库。
        )
        if query.keyword:  # 未提供关键字或为空字符串时，不追加模糊查询。
            like = f"%{query.keyword}%"  # 百分号表示任意字符，由 ORM 绑定参数。
            stmt = stmt.where(  # 追加关键字条件，与其他筛选条件同时满足。
                or_(  # 编码或名称任一匹配即可。
                    BdWarehouse.code.ilike(like),  # 编码包含关键字，不区分大小写。
                    BdWarehouse.name.ilike(like),  # 名称使用相同的模糊匹配方式。
                )  # 结束编码、名称的或条件组合。
            )  # 将关键字条件附加到查询语句。
        if query.status is not None:  # 0 表示停用，不能用 if query.status 判断。
            stmt = stmt.where(BdWarehouse.status == query.status)  # 精确匹配状态。
        return stmt.order_by(BdWarehouse.id.desc())  # ID 倒序；分页复用父类 paginate。

    async def get_default(  # 查询当前默认仓，供切换默认仓时参考。
        self,  # 使用当前仓库查询会话。
        exclude_id: int | None = None,  # 可排除正在编辑或设置的仓库。
    ) -> BdWarehouse | None:  # 没有符合条件的默认仓时返回 None。
        """查询未删除的默认仓，可按 exclude_id 排除自身。

        多条符合条件的记录会触发查询异常，不静默挑选其中一条。
        默认仓切换与并发控制由 Service 负责，此处仅查询。
        """
        stmt = select(BdWarehouse).where(  # 同时满足以下默认仓条件，并按租户隔离。
            BdWarehouse.is_deleted.is_(False),  # 已删除仓库不再作为当前默认仓。
            BdWarehouse.is_default.is_(True),  # 要求默认仓标记为 True。
            BdWarehouse.tenant_id == self.tenant_id,  # 租户隔离，只查当前租户默认仓。
        )  # where 中的多个条件默认按 AND 组合。
        if exclude_id is not None:  # 仅指定 ID 时排除当前仓库。
            stmt = stmt.where(BdWarehouse.id != exclude_id)  # 查询其他默认仓。
        return (await self.db.execute(stmt)).scalar_one_or_none()  # 返回对象或 None。

    async def clear_defaults(  # 批量取消其他仓库的默认标记，供 Service 原子切换默认仓。
        self,  # 使用调用方传入的同一事务会话。
        operator_id: int,  # 记录被取消默认的旧仓库由谁更新。
        exclude_id: int | None = None,  # 修改时保留目标仓库，交给 Service 更新。
    ) -> list[int]:  # 返回原默认仓 ID，供同一事务记录变更日志。
        """取消未删除仓库的默认标记，记录 operator_id；exclude_id 可排除目标，返回被改 ID。

        UPDATE 在当前事务内执行，不提交；若后续新增、修改或日志失败，Service 可整体回滚。
        """
        stmt = update(BdWarehouse).where(  # 只更新当前真正作为默认仓的记录，并按租户隔离。
            BdWarehouse.is_deleted.is_(False),  # 不改动已经软删除的历史档案。
            BdWarehouse.is_default.is_(True),  # 非默认仓无需重复更新审计字段。
            BdWarehouse.tenant_id == self.tenant_id,  # 租户隔离，只取消当前租户默认仓。
        )  # 一次更新全部旧默认，避免遗漏不一致的历史默认标记。
        if exclude_id is not None:  # 修改现有仓库时，目标仓由 Service 自己更新。
            stmt = stmt.where(BdWarehouse.id != exclude_id)  # 不取消目标仓已有的默认标记。
        # 同时更新默认标记和操作人，并返回受影响的仓库 ID。
        stmt = stmt.values(is_default=False, updated_by=operator_id).returning(BdWarehouse.id)
        ids = list((await self.db.execute(stmt)).scalars().all())  # UPDATE 已执行，但还未提交。
        await self.db.flush()  # 仅同步当前事务，最终提交或回滚仍由 Service 决定。
        return sorted(ids)  # 稳定排序，便于在操作日志中核对旧默认仓。

    async def list_manager_names(  # 一次查询所有负责人，避免逐个访问数据库。
        self,  # 使用当前事务会话查询关联用户。
        user_ids: list[int],  # 由 Service 收集当前页非空的负责人 ID。
    ) -> dict[int, str | None]:  # 返回用户 ID 到姓名的映射，姓名允许为空。
        """按 user_ids 批量返回未删除用户的真实姓名；未填写姓名保留 None。"""
        if not user_ids:  # 当前页无负责人时，不必发送数据库查询。
            return {}  # 空映射可直接交给 Service 做名称回填。
        stmt = select(SysUser.id, SysUser.real_name).where(  # 只取回填名称所需字段。
            SysUser.id.in_(user_ids),  # IN 条件一次覆盖所有传入 ID。
            SysUser.tenant_id == self.tenant_id,
            SysUser.is_deleted.is_(False),  # 排除已删除用户，保留未删除的停用用户。
        )  # 不筛账号状态，避免停用账号后无法显示原负责人。
        rows = (await self.db.execute(stmt)).tuples().all()  # 保留两列及其类型，便于转字典。
        return dict(rows)  # 每行的 ID 为键、姓名为值；姓名可为 None，缺失 ID 不入字典。

    async def list_manager_options(self, keyword: str | None = None) -> list[dict]:
        """仓库专用最小用户选项；不暴露手机号、角色等系统用户资料。"""
        stmt = select(SysUser.id, SysUser.real_name, SysUser.username).where(
            SysUser.tenant_id == self.tenant_id,
            SysUser.is_deleted.is_(False),
            SysUser.status == 1,
        )
        if keyword:
            stmt = stmt.where(
                or_(
                    SysUser.real_name.ilike(f"%{keyword}%"),
                    SysUser.username.ilike(f"%{keyword}%"),
                )
            )
        rows = (await self.db.execute(stmt.order_by(SysUser.id).limit(100))).all()
        return [{"value": row.id, "label": row.real_name or row.username} for row in rows]

    async def has_stock(self, warehouse_id: int) -> bool:  # 查询指定仓库是否仍有库存。
        """返回仓库是否存在未删除且账面数量或锁定数量非零的库存记录。

        按行检查可防止正负库存相抵；负数或锁定库存也不能被当作空仓。
        全零记录不视为仍有库存；停用、删除限制与未结单据校验由 Service 处理。
        """
        stock_query = select(InvStock.id).where(  # 只描述需要检查的库存记录，并按租户隔离。
            InvStock.warehouse_id == warehouse_id,  # 只检查指定仓库。
            InvStock.tenant_id == self.tenant_id,  # 租户隔离，只检查当前租户库存。
            InvStock.is_deleted.is_(False),  # 已软删除的库存记录不参与判断。
            or_(  # 账面或锁定数量任一非零，就说明不能当作空仓。
                InvStock.qty != 0,  # 账面数量含负数，避免漏检负库存。
                InvStock.locked_qty != 0,  # 有锁定数量时同样保留仓库。
            ),  # 结束两类数量的或条件。
        )  # 不跨商品汇总，避免正数与负数抵消。
        stmt = select(stock_query.exists())  # EXISTS 检查存在性，不加载全部库存。
        return bool((await self.db.execute(stmt)).scalar_one())  # 返回单个布尔值。
