"""建表与种子数据（首次启动自动执行，幂等）。"""

from sqlalchemy import func, select
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


DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"


async def init_db() -> None:
    """建表并写入种子数据。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        await _seed(session)


async def _seed(session: AsyncSession) -> None:
    exists = await session.scalar(select(func.count()).select_from(SysUser))
    if exists:
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

    buttons = [
        _button("新增用户", "sys:user:add", user_menu.id, 1),
        _button("修改用户", "sys:user:edit", user_menu.id, 2),
        _button("删除用户", "sys:user:delete", user_menu.id, 3),
        _button("重置密码", "sys:user:reset", user_menu.id, 4),
        _button("角色分配", "sys:role:assign", role_menu.id, 5),
        _button("新增商品", "base:goods:add", goods_menu.id, 1),
        _button("修改商品", "base:goods:edit", goods_menu.id, 2),
        _button("删除商品", "base:goods:delete", goods_menu.id, 3),
        _button("导出商品", "base:goods:export", goods_menu.id, 4),
        _button("导入商品", "base:goods:import", goods_menu.id, 5),
        _button("商品分类管理", "base:category:list", goods_menu.id, 6),
        _button("计量单位管理", "base:unit:list", goods_menu.id, 7),
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
        ("GD0001", "无线鼠标", "2.4G黑色", category_electronics.id, unit_piece.id, "6901234567890", 35, 69, 10, 200),
        ("GD0002", "机械键盘", "87键青轴", category_electronics.id, unit_piece.id, "6901234567891", 120, 239, 5, 100),
        ("GD0003", "笔记本电脑", "14英寸/i5/16G", category_electronics.id, unit_set.id, "6901234567892", 3800, 4599, 2, 50),
        ("GD0004", "A4打印纸", "70g/500张", category_office.id, unit_box.id, "6901234567893", 18, 32, 20, 500),
        ("GD0005", "签字笔", "0.5mm黑色", category_office.id, unit_box.id, "6901234567894", 9.5, 19.9, 50, 1000),
        ("GD0006", "文件收纳盒", "尺寸40cm", category_office.id, unit_piece.id, "6901234567895", 12, 25, 10, 200),
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
            for code, name, spec, category_id, unit_id, barcode, cost_price, sale_price, stock_lower, stock_upper in goods_rows
        ]
    )
    await session.flush()
