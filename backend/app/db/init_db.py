"""安全初始化空库与种子数据；已有数据库的结构变更必须显式执行迁移。

只有完全空的数据库可以自动执行 Alembic 迁移。已有未纳管数据库、版本
落后或迁移失败均阻断启动，绝不通过 create_all / stamp 掩盖结构差异。
"""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
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


def _alembic_config() -> Config:
    cfg = Config(str(ALEMBIC_INI))
    # 启动目录可能不是 backend，迁移脚本路径不能依赖当前工作目录。
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    return cfg


def _alembic_head() -> str:
    """读取唯一迁移 head；缺失或分叉都必须阻断启动。"""
    try:
        head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    except Exception as exc:
        raise RuntimeError("无法读取 Alembic head；请检查迁移脚本后再启动") from exc
    if not head:
        raise RuntimeError("Alembic head 为空；请检查迁移脚本后再启动")
    return head


def _current_revision(connection) -> str | None:
    """无版本记录返回 None；多版本记录不猜测、不自动合并。"""
    if "alembic_version" not in inspect(connection).get_table_names():
        return None
    rows = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    if len(rows) > 1:
        raise RuntimeError("数据库包含多个 Alembic 版本；请核对后执行 alembic upgrade head")
    return rows[0] if rows else None


def _upgrade_to_head(connection) -> None:
    """复用空库初始化事务，真正执行结构迁移及迁移中的字典/参数种子。"""
    cfg = _alembic_config()
    cfg.attributes["connection"] = connection
    try:
        command.upgrade(cfg, "head")
    except Exception as exc:
        raise RuntimeError(
            "数据库迁移失败，启动已终止且未 stamp；请修复后执行 alembic upgrade head"
        ) from exc


def _prepare_schema(connection) -> None:
    head = _alembic_head()
    revision = _current_revision(connection)
    if revision is not None:
        if revision != head:
            raise RuntimeError(
                f"数据库迁移版本 {revision} 与代码 head {head} 不一致；"
                "请先执行 alembic upgrade head，启动不会 create_all 或 stamp"
            )
        return

    inspector = inspect(connection)
    # 空的 alembic_version 表可以是失败迁移留下的；只要有业务表/视图就不算空库。
    tables = set(inspector.get_table_names()) - {"alembic_version"}
    if tables or inspector.get_view_names():
        raise RuntimeError(
            "检测到已有未纳管数据库（存在表/视图但无 Alembic 版本）；"
            "请核对历史结构与迁移基线后执行 alembic upgrade head，禁止自动 stamp"
        )
    _upgrade_to_head(connection)
    if _current_revision(connection) != head:
        raise RuntimeError(
            "数据库迁移未达到代码 head；请执行 alembic upgrade head，禁止自动 stamp"
        )
    logger.info("空数据库已通过 Alembic 迁移到 head: %s", head)


async def init_db() -> None:
    """仅初始化空库；已纳管数据库必须位于 head 才允许写入应用种子。"""
    async with engine.begin() as conn:
        await conn.run_sync(_prepare_schema)
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
