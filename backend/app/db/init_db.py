"""建表与种子数据（首次启动自动执行，幂等）。

schema 策略（与 alembic 配合，结构变更一律走迁移）：
- 新库（无 alembic_version 表）：create_all 建表后 stamp 到当前 head，
  之后的结构变更由 `alembic upgrade head` 负责；
- 已有库（有 alembic_version）：只补建缺失的表，**绝不修改已有表结构**；
  若库里的版本落后于代码 head，打印告警提示执行 `alembic upgrade head`。
"""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine
from app.models.base_data import BdGoods, BdGoodsCategory, BdUnit, BdWarehouse
from app.models.sys import (
    SysDept,
    SysPermission,
    SysRole,
    SysRolePermission,
    SysUser,
    SysUserRole,
)


logger = logging.getLogger(__name__)

# backend/alembic.ini（app/db/init_db.py -> backend/）
ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"


def _alembic_script() -> ScriptDirectory | None:
    """加载 alembic 脚本目录；失败返回 None（不阻断服务启动）。"""
    try:
        cfg = Config(str(ALEMBIC_INI))
        return ScriptDirectory.from_config(cfg)
    except Exception:  # noqa: BLE001  迁移目录缺失时不应影响服务启动
        logger.warning("未能加载 alembic 脚本目录，请检查 %s", ALEMBIC_INI, exc_info=True)
        return None


def _alembic_head() -> str | None:
    """读取代码侧当前迁移版本（head）；读不到返回 None。"""
    script = _alembic_script()
    if script is None:
        return None
    try:
        return script.get_current_head()
    except Exception:  # noqa: BLE001
        logger.warning("未能读取 alembic head", exc_info=True)
        return None


def _current_revision(connection) -> str | None:
    """读取数据库中记录的迁移版本；没有版本表（全新库）返回 None。"""
    if "alembic_version" not in inspect(connection).get_table_names():
        return None
    rows = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    return rows[0] if rows else None


def _upgrade_to_head(connection) -> bool:
    """在已建表的库上执行 alembic 迁移，把版本推进到 head。

    背景：新库的建表走 Base.metadata.create_all，而各迁移文件里除了建表，
    还包含**种子数据**（字典类型/字典项、系统参数、单号规则等）的 INSERT。
    只 create_all + stamp 会跳过这些 INSERT，导致新库缺少字典与参数，
    字典页、单号规则接口直接 404。

    这里让迁移真正跑一遍：迁移脚本里的建表操作都做了「表已存在则跳过」的
    幂等处理，因此可以安全地在 create_all 之后执行。

    返回是否成功推进到 head；失败不抛异常，仅告警，避免阻断服务启动。
    """
    script = _alembic_script()
    if script is None:
        return False
    try:
        cfg = Config(str(ALEMBIC_INI))
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")
    except Exception:  # noqa: BLE001  迁移失败不应阻断服务启动
        logger.warning("执行 alembic upgrade head 失败", exc_info=True)
        return False
    return True


def _stamp_head(connection) -> None:
    """新库建表后打基线：把版本标记为当前 head，后续变更走迁移。"""
    script = _alembic_script()
    if script is None:
        return
    head = script.get_current_head()
    if not head:
        return
    # 复用外层事务，不额外开启事务
    MigrationContext.configure(connection).stamp(script, revision=head)
    logger.info("已创建 alembic 基线版本：%s", head)


async def init_db() -> None:
    """建表并写入种子数据。

    库表结构策略（与 alembic 配合，结构变更一律走迁移）：
    - 新库（无 alembic_version 表）：create_all 建表后 stamp 到当前 head，
      之后的结构变更由 `alembic upgrade head` 负责；
    - 已有库（有 alembic_version）：不再 create_all 改结构，仅将版本标记为
      当前 head，结构变更由 CI/部署脚本中的 `alembic upgrade head` 负责。
    """
    async with engine.begin() as conn:
        revision = await conn.run_sync(_current_revision)
        if revision is None:
            # 全新库：先 create_all 建表，再执行迁移以补齐种子数据
            # （迁移里含字典/参数/单号规则的 INSERT，只 create_all 会漏掉）
            await conn.run_sync(Base.metadata.create_all)
            upgraded = await conn.run_sync(_upgrade_to_head)
            if upgraded:
                logger.info("新库已建表并执行迁移到 alembic head")
            else:
                # 迁移不可用时退化为仅打基线，保证服务仍能启动
                await conn.run_sync(_stamp_head)
                logger.info("新库已建表并 stamp 到 alembic head（迁移未执行）")
        else:
            # 已纳管的库：仍补建元数据中存在但库里缺失的表（如后加的财务表），
            # create_all 幂等、只增不删不改已有结构；随后将版本标记为 head
            await conn.run_sync(Base.metadata.create_all)
            head = _alembic_head()
            if head:
                if revision != head:
                    logger.warning(
                        "数据库迁移版本 %s 落后于代码 head %s；"
                        "init_db 不再自动改结构，请确保已通过 `alembic upgrade head` "
                        "应用结构变更；此处仅将版本标记为 head",
                        revision,
                        head,
                    )
                await conn.run_sync(_stamp_head)
                logger.info("已将数据库版本标记为 alembic head: %s", head)
            else:
                logger.warning("无法读取 alembic head，跳过 stamp")
    async with AsyncSessionLocal() as session:
        await _seed(session)


async def _seed(session: AsyncSession) -> None:
    exists = await session.scalar(select(func.count()).select_from(SysUser))
    if exists:
        # 已有数据库不重复写入基础数据，只补齐后续新增的权限项，保证按钮权限可用
        await _ensure_permissions(session)
        await session.commit()
        return

    dept = SysDept(name="总公司", parent_id=None, leader="系统管理员", sort=1, status=1)
    session.add(dept)
    await session.flush()

    permissions = await _seed_permissions(session)
    admin_role = await _seed_roles(session, permissions)
    await _seed_admin(session, dept.id, admin_role.id)
    await _seed_base_data(session)

    await session.commit()


async def _seed_permissions(session: AsyncSession) -> list[SysPermission]:
    dir_sys = _dir("系统管理", "/system", "Setting", 1)
    dir_base = _dir("基础资料", "/base", "Goods", 2)
    dir_purchase = _dir("采购管理", "/purchase", "ShoppingCart", 3)
    dir_sales = _dir("销售管理", "/sales", "Sell", 4)
    dir_inventory = _dir("库存管理", "/inventory", "Box", 5)
    dir_finance = _dir("财务管理", "/finance", "Money", 6)
    dir_report = _dir("报表中心", "/report", "DataLine", 7)
    session.add_all(
        [dir_sys, dir_base, dir_purchase, dir_sales, dir_inventory, dir_finance, dir_report]
    )
    await session.flush()

    menus = [
        # 经营看板（根级菜单）
        SysPermission(
            name="经营看板",
            type=2,
            perm_code="rpt:dashboard:list",
            route_path="/dashboard",
            component="dashboard/DashboardPage",
            icon="Odometer",
            sort=0,
        ),
        # 基础资料
        _menu("商品管理", "base:goods:list", "/base/goods", dir_base.id, 1),
        _menu("仓库管理", "base:warehouse:list", "/base/warehouse", dir_base.id, 2),
        _menu("供应商管理", "base:supplier:list", "/base/supplier", dir_base.id, 3),
        _menu("客户管理", "base:customer:list", "/base/customer", dir_base.id, 4),
        _menu("员工/部门", "base:employee:list", "/base/employee", dir_base.id, 5),
        _menu("数据字典", "base:dict:list", "/base/dict", dir_base.id, 6),
        # 采购管理
        _menu("采购申请", "pur:apply:list", "/purchase/apply", dir_purchase.id, 1),
        _menu("采购订单", "pur:order:list", "/purchase/order", dir_purchase.id, 2),
        _menu("采购入库", "pur:inbound:list", "/purchase/inbound", dir_purchase.id, 3),
        _menu("采购退货", "pur:return:list", "/purchase/return", dir_purchase.id, 4),
        # 销售管理
        _menu("销售报价", "sal:quotation:list", "/sales/quotation", dir_sales.id, 1),
        _menu("销售订单", "sal:order:list", "/sales/order", dir_sales.id, 2),
        _menu("销售出库", "sal:outbound:list", "/sales/outbound", dir_sales.id, 3),
        _menu("销售退货", "sal:return:list", "/sales/return", dir_sales.id, 4),
        # 库存管理
        _menu("实时库存", "inv:stock:list", "/inventory/stock", dir_inventory.id, 1),
        _menu("出入库流水", "inv:flow:list", "/inventory/flow", dir_inventory.id, 2),
        _menu("库存调拨", "inv:transfer:list", "/inventory/transfer", dir_inventory.id, 3),
        _menu("库存盘点", "inv:check:list", "/inventory/check", dir_inventory.id, 4),
        _menu("库存预警", "inv:alert:list", "/inventory/alert", dir_inventory.id, 5),
        # 财务管理
        _menu("应收管理", "fin:receivable:list", "/finance/receivable", dir_finance.id, 1),
        _menu("应付管理", "fin:payable:list", "/finance/payable", dir_finance.id, 2),
        _menu("收款单", "fin:receipt:list", "/finance/receipt", dir_finance.id, 3),
        _menu("付款单", "fin:payment:list", "/finance/payment", dir_finance.id, 4),
        _menu("费用管理", "fin:expense:list", "/finance/expense", dir_finance.id, 5),
        _menu("对账单", "fin:statement:list", "/finance/statement", dir_finance.id, 6),
        # 报表中心
        _menu("采购报表", "rpt:purchase:list", "/report/purchase", dir_report.id, 1),
        _menu("销售报表", "rpt:sales:list", "/report/sales", dir_report.id, 2),
        _menu("库存报表", "rpt:inventory:list", "/report/inventory", dir_report.id, 3),
        _menu("财务报表", "rpt:finance:list", "/report/finance", dir_report.id, 4),
        # 系统管理
        _menu("用户管理", "sys:user:list", "/system/user", dir_sys.id, 1),
        _menu("角色权限", "sys:role:list", "/system/role", dir_sys.id, 2),
        _menu("操作日志", "sys:log:list", "/system/log", dir_sys.id, 3),
        _menu("系统参数", "sys:config:list", "/system/config", dir_sys.id, 4),
    ]
    session.add_all(menus)
    await session.flush()

    user_menu = next(menu for menu in menus if menu.perm_code == "sys:user:list")
    role_menu = next(menu for menu in menus if menu.perm_code == "sys:role:list")
    goods_menu = next(menu for menu in menus if menu.perm_code == "base:goods:list")
    warehouse_menu = next(menu for menu in menus if menu.perm_code == "base:warehouse:list")
    supplier_menu = next(menu for menu in menus if menu.perm_code == "base:supplier:list")
    customer_menu = next(menu for menu in menus if menu.perm_code == "base:customer:list")

    buttons = [
        _button("新增用户", "sys:user:add", user_menu.id, 1),
        _button("修改用户", "sys:user:edit", user_menu.id, 2),
        _button("删除用户", "sys:user:delete", user_menu.id, 3),
        _button("重置密码", "sys:user:reset", user_menu.id, 4),
        _button("新增角色", "sys:role:add", role_menu.id, 2),
        _button("修改角色", "sys:role:edit", role_menu.id, 3),
        _button("删除角色", "sys:role:delete", role_menu.id, 4),
        _button("角色分配", "sys:role:assign", role_menu.id, 5),
        _button("新增商品", "base:goods:add", goods_menu.id, 1),
        _button("修改商品", "base:goods:edit", goods_menu.id, 2),
        _button("删除商品", "base:goods:delete", goods_menu.id, 3),
        _button("导出商品", "base:goods:export", goods_menu.id, 4),
        _button("导入商品", "base:goods:import", goods_menu.id, 5),
        _button("商品分类管理", "base:category:list", goods_menu.id, 6),
        _button("计量单位管理", "base:unit:list", goods_menu.id, 7),
        _button("新增仓库", "base:warehouse:add", warehouse_menu.id, 1),
        _button("修改仓库", "base:warehouse:edit", warehouse_menu.id, 2),
        _button("删除仓库", "base:warehouse:delete", warehouse_menu.id, 3),
        _button("新增供应商", "base:supplier:add", supplier_menu.id, 1),
        _button("修改供应商", "base:supplier:edit", supplier_menu.id, 2),
        _button("删除供应商", "base:supplier:delete", supplier_menu.id, 3),
        _button("新增客户", "base:customer:add", customer_menu.id, 1),
        _button("修改客户", "base:customer:edit", customer_menu.id, 2),
        _button("删除客户", "base:customer:delete", customer_menu.id, 3),
        _button("查看操作日志", "sys:log:view", user_menu.id, 6),
        _button("查看异步任务", "sys:task:view", user_menu.id, 7),
        _button("超级权限", "*", dir_sys.id, 99),
    ]
    session.add_all(buttons)
    await session.flush()

    for code in (
        "base:category:add",
        "base:category:edit",
        "base:category:delete",
        "base:unit:add",
        "base:unit:edit",
        "base:unit:delete",
    ):
        session.add(
            SysPermission(name=code, type=3, perm_code=code, parent_id=goods_menu.id)
        )
    await session.flush()

    return list((await session.execute(select(SysPermission))).scalars().all())


def _dir(name: str, route_path: str, icon: str, sort: int) -> SysPermission:
    """构造目录权限。"""
    return SysPermission(name=name, type=1, route_path=route_path, icon=icon, sort=sort)


def _menu(
    name: str, perm_code: str, route_path: str, parent_id: int, sort: int
) -> SysPermission:
    """构造菜单权限，组件路径由路由路径推导（/base/goods → base/goods/index）。"""
    return SysPermission(
        name=name,
        type=2,
        perm_code=perm_code,
        route_path=route_path,
        component=f"{route_path.strip('/')}/index",
        parent_id=parent_id,
        sort=sort,
    )


def _button(name: str, perm_code: str, parent_id: int, sort: int) -> SysPermission:
    """构造按钮权限。"""
    return SysPermission(name=name, type=3, perm_code=perm_code, parent_id=parent_id, sort=sort)


async def _seed_roles(session: AsyncSession, permissions: list[SysPermission]) -> SysRole:
    admin_role = SysRole(code="admin", name="超级管理员", data_scope=3, status=1, remark="内置角色")
    viewer_role = SysRole(code="viewer", name="只读用户", data_scope=1, status=1, remark="内置角色")
    session.add_all([admin_role, viewer_role])
    await session.flush()

    for perm in permissions:
        session.add(SysRolePermission(role_id=admin_role.id, permission_id=perm.id))
    viewer_codes = {
        "rpt:dashboard:list",
        "sys:user:list",
        "base:goods:list",
        "base:warehouse:list",
        "base:supplier:list",
        "base:customer:list",
        "inv:stock:list",
        "rpt:purchase:list",
        "rpt:sales:list",
    }
    for perm in permissions:
        if perm.perm_code in viewer_codes:
            session.add(SysRolePermission(role_id=viewer_role.id, permission_id=perm.id))
    await session.flush()
    return admin_role


async def _seed_admin(session: AsyncSession, dept_id: int, admin_role_id: int) -> None:
    admin = SysUser(
        username=DEFAULT_ADMIN_USERNAME,
        password_hash=hash_password(DEFAULT_ADMIN_PASSWORD),
        real_name="系统管理员",
        dept_id=dept_id,
        position="系统管理员",
        status=1,
    )
    session.add(admin)
    await session.flush()
    session.add(SysUserRole(user_id=admin.id, role_id=admin_role_id))


async def _seed_base_data(session: AsyncSession) -> None:
    category_electronics = BdGoodsCategory(name="电子产品", sort=1)
    category_office = BdGoodsCategory(name="办公用品", sort=2)
    unit_piece = BdUnit(name="个", precision=0)
    unit_set = BdUnit(name="台", precision=0)
    unit_box = BdUnit(name="箱", precision=0)
    session.add_all(
        [
            BdWarehouse(code="WH0001", name="主仓库", is_default=True, status=1),
            category_electronics,
            category_office,
            unit_piece,
            unit_set,
            unit_box,
        ]
    )
    await session.flush()

    goods_rows = [
        (
            "GD0001", "无线鼠标", "2.4G黑色", category_electronics.id, unit_piece.id,
            "6901234567890", 35, 69, 10, 200,
        ),
        (
            "GD0002", "机械键盘", "87键青轴", category_electronics.id, unit_piece.id,
            "6901234567891", 120, 239, 5, 100,
        ),
        (
            "GD0003", "笔记本电脑", "14英寸/i5/16G", category_electronics.id, unit_set.id,
            "6901234567892", 3800, 4599, 2, 50,
        ),
        (
            "GD0004", "A4打印纸", "70g/500张", category_office.id, unit_box.id,
            "6901234567893", 18, 32, 20, 500,
        ),
        (
            "GD0005", "签字笔", "0.5mm黑色", category_office.id, unit_box.id,
            "6901234567894", 9.5, 19.9, 50, 1000,
        ),
        (
            "GD0006", "文件收纳盒", "尺寸40cm", category_office.id, unit_piece.id,
            "6901234567895", 12, 25, 10, 200,
        ),
    ]
    session.add_all(
        [
            BdGoods(
                code=code,
                name=name,
                spec=spec,
                category_id=category_id,
                unit_id=unit_id,
                barcode=barcode,
                cost_price=cost_price,
                sale_price=sale_price,
                stock_lower=stock_lower,
                stock_upper=stock_upper,
                status=1,
            )
            for (
                code, name, spec, category_id, unit_id,
                barcode, cost_price, sale_price, stock_lower, stock_upper,
            ) in goods_rows
        ]
    )
    await session.flush()


async def _ensure_permissions(session: AsyncSession) -> None:
    """幂等补齐缺失的权限项，并授权给超级管理员角色。

    种子数据只在首次初始化时写入，后续新增的权限项需要在此补齐，
    否则已初始化的数据库会缺少按钮权限，前端 v-perm 会隐藏对应操作。
    """
    # 供应商菜单：更早版本建立的数据库可能还没有这一项
    supplier_menu = await session.scalar(
        select(SysPermission).where(SysPermission.perm_code == "base:supplier:list")
    )
    if supplier_menu is None:
        # 找到基础资料目录，作为菜单的父级
        dir_base = await session.scalar(
            select(SysPermission).where(
                SysPermission.type == 1, SysPermission.route_path == "/base"
            )
        )
        supplier_menu = SysPermission(
            name="供应商管理",
            type=2,
            perm_code="base:supplier:list",
            route_path="/base/supplier",
            component="base/supplier/index",
            parent_id=dir_base.id if dir_base is not None else None,
            sort=3,
        )
        session.add(supplier_menu)
        await session.flush()

    # 供应商按钮权限：新增、修改、删除
    button_specs = (
        ("新增供应商", "base:supplier:add", 1),
        ("修改供应商", "base:supplier:edit", 2),
        ("删除供应商", "base:supplier:delete", 3),
    )
    for name, perm_code, sort in button_specs:
        exists = await session.scalar(
            select(SysPermission.perm_code).where(SysPermission.perm_code == perm_code)
        )
        if exists is None:
            session.add(
                SysPermission(
                    name=name,
                    type=3,
                    perm_code=perm_code,
                    parent_id=supplier_menu.id,
                    sort=sort,
                )
            )
    await session.flush()

    # 超级管理员默认拥有全部权限，补齐权限项后需要同步授权
    admin_role = await session.scalar(select(SysRole).where(SysRole.code == "admin"))
    if admin_role is None:
        return
    supplier_perms = list(
        (
            await session.execute(
                select(SysPermission).where(SysPermission.perm_code.like("base:supplier:%"))
            )
        ).scalars().all()
    )
    for perm in supplier_perms:
        granted = await session.scalar(
            select(SysRolePermission.role_id).where(
                SysRolePermission.role_id == admin_role.id,
                SysRolePermission.permission_id == perm.id,
            )
        )
        if granted is None:
            session.add(SysRolePermission(role_id=admin_role.id, permission_id=perm.id))
    await session.flush()

    # 客户菜单与按钮权限：首次初始化已播种菜单，这里对老库补齐菜单与按钮，并授权管理员
    customer_menu = await session.scalar(
        select(SysPermission).where(SysPermission.perm_code == "base:customer:list")
    )
    if customer_menu is None:
        dir_base = await session.scalar(
            select(SysPermission).where(
                SysPermission.type == 1, SysPermission.route_path == "/base"
            )
        )
        customer_menu = SysPermission(
            name="客户管理",
            type=2,
            perm_code="base:customer:list",
            route_path="/base/customer",
            component="base/customer/index",
            parent_id=dir_base.id if dir_base is not None else None,
            sort=4,
        )
        session.add(customer_menu)
        await session.flush()

    customer_button_specs = (
        ("新增客户", "base:customer:add", 1),
        ("修改客户", "base:customer:edit", 2),
        ("删除客户", "base:customer:delete", 3),
    )
    for name, perm_code, sort in customer_button_specs:
        exists = await session.scalar(
            select(SysPermission.perm_code).where(SysPermission.perm_code == perm_code)
        )
        if exists is None:
            session.add(
                SysPermission(
                    name=name,
                    type=3,
                    perm_code=perm_code,
                    parent_id=customer_menu.id,
                    sort=sort,
                )
            )
    await session.flush()

    # 超级管理员默认拥有全部权限，补齐后同步授权
    customer_perms = list(
        (
            await session.execute(
                select(SysPermission).where(SysPermission.perm_code.like("base:customer:%"))
            )
        ).scalars().all()
    )
    for perm in customer_perms:
        granted = await session.scalar(
            select(SysRolePermission.role_id).where(
                SysRolePermission.role_id == admin_role.id,
                SysRolePermission.permission_id == perm.id,
            )
        )
        if granted is None:
            session.add(SysRolePermission(role_id=admin_role.id, permission_id=perm.id))
    await session.flush()
