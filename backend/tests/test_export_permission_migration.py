"""SQLite coverage for the idempotent export-permission seed revision."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "d4e9a1b7c3f5_export_permissions.py"
)
spec = importlib.util.spec_from_file_location("export_permission_migration", MIGRATION_PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

EXPORT_CODES = ("inv:stock:export", "report:export", "fin:statement:export")


@pytest.fixture
def connection():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        for statement in (
            "CREATE TABLE sys_permission ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER, name TEXT NOT NULL, "
            "type INTEGER NOT NULL, perm_code TEXT, route_path TEXT, component TEXT, icon TEXT, "
            "sort INTEGER NOT NULL DEFAULT 0, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, is_deleted BOOLEAN NOT NULL DEFAULT 0)",
            "CREATE TABLE sys_role ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, code TEXT NOT NULL, "
            "name TEXT NOT NULL, data_scope INTEGER NOT NULL, status INTEGER NOT NULL, "
            "is_deleted BOOLEAN NOT NULL DEFAULT 0)",
            "CREATE TABLE sys_role_permission ("
            "role_id INTEGER NOT NULL, permission_id INTEGER NOT NULL, "
            "PRIMARY KEY (role_id, permission_id))",
            "CREATE TABLE unrelated (id INTEGER PRIMARY KEY, value TEXT NOT NULL)",
        ):
            conn.exec_driver_sql(statement)
        yield conn
    engine.dispose()


def _run(conn, direction="upgrade"):
    with Operations.context(MigrationContext.configure(conn)):
        getattr(migration, direction)()


def _permission_rows(conn):
    return conn.execute(
        sa.text(
            "SELECT id, name, type, perm_code, parent_id, sort, is_deleted "
            "FROM sys_permission ORDER BY id"
        )
    ).mappings().all()


def _grant_rows(conn):
    return conn.exec_driver_sql(
        "SELECT role_id, permission_id FROM sys_role_permission ORDER BY role_id, permission_id"
    ).all()


def _insert_role(conn, role_id, tenant_id, *, code="admin", status=1, is_deleted=0):
    conn.execute(
        sa.text(
            "INSERT INTO sys_role "
            "(id, tenant_id, code, name, data_scope, status, is_deleted) "
            "VALUES (:id, :tenant, :code, :name, 3, :status, :deleted)"
        ),
        {
            "id": role_id,
            "tenant": tenant_id,
            "code": code,
            "name": f"role-{role_id}",
            "status": status,
            "deleted": is_deleted,
        },
    )


def _insert_permission(
    conn,
    permission_id,
    code,
    *,
    name=None,
    type_=3,
    parent_id=None,
    route_path=None,
    sort=0,
    is_deleted=0,
):
    conn.execute(
        sa.text(
            "INSERT INTO sys_permission "
            "(id, parent_id, name, type, perm_code, route_path, sort, is_deleted) "
            "VALUES (:id, :parent, :name, :type, :code, :route, :sort, :deleted)"
        ),
        {
            "id": permission_id,
            "parent": parent_id,
            "name": name or code or f"permission-{permission_id}",
            "type": type_,
            "code": code,
            "route": route_path,
            "sort": sort,
            "deleted": is_deleted,
        },
    )


def _canonical_ids(conn):
    return dict(
        conn.execute(
            sa.text(
                "SELECT perm_code, MIN(id) FROM sys_permission "
                "WHERE perm_code IN :codes GROUP BY perm_code"
            ).bindparams(sa.bindparam("codes", expanding=True)),
            {"codes": EXPORT_CODES},
        ).all()
    )


def test_upgrade_empty_permissions_grants_all_enabled_tenant_admins(connection):
    conn = connection
    _insert_role(conn, 1, 10)
    _insert_role(conn, 2, 20)
    _insert_role(conn, 3, 30, status=0)
    _insert_role(conn, 4, 40, is_deleted=1)
    _insert_role(conn, 5, 50, code="viewer")
    conn.exec_driver_sql("INSERT INTO unrelated VALUES (1, 'keep-me')")

    _run(conn)

    rows = _permission_rows(conn)
    assert [row["perm_code"] for row in rows] == list(EXPORT_CODES)
    assert all(row["type"] == 3 and row["is_deleted"] == 0 for row in rows)
    assert all(row["parent_id"] is None for row in rows)
    canonical = _canonical_ids(conn)
    assert _grant_rows(conn) == sorted(
        (role_id, canonical[code]) for role_id in (1, 2) for code in EXPORT_CODES
    )
    assert conn.exec_driver_sql("SELECT value FROM unrelated WHERE id=1").scalar_one() == "keep-me"


def test_upgrade_partial_data_uses_available_parents_and_keeps_existing_rows(connection):
    conn = connection
    _insert_role(conn, 1, 1)
    _insert_permission(conn, 10, "inv:stock:list", name="实时库存", type_=2)
    _insert_permission(conn, 15, "rpt:purchase:list", name="采购报表", type_=2)
    _insert_permission(conn, 20, None, name="报表中心", type_=1, route_path="/report")
    _insert_permission(conn, 25, "rpt:sales:list", name="销售报表", type_=2)
    _insert_permission(conn, 30, "fin:statement:list", name="对账单", type_=2)
    _insert_permission(
        conn,
        40,
        "historical:keep",
        name="历史权限",
        parent_id=999,
        sort=77,
        is_deleted=1,
    )
    _insert_permission(conn, 50, "unrelated:keep", name="不要修改", sort=13)
    before_existing = conn.exec_driver_sql(
        "SELECT id, parent_id, name, type, perm_code, sort, is_deleted "
        "FROM sys_permission WHERE id IN (40, 50) ORDER BY id"
    ).all()

    _run(conn)

    by_code = {
        row["perm_code"]: row
        for row in _permission_rows(conn)
        if row["perm_code"] in EXPORT_CODES
    }
    assert by_code["inv:stock:export"]["parent_id"] == 10
    assert by_code["report:export"]["parent_id"] == 20
    assert by_code["fin:statement:export"]["parent_id"] == 30
    assert (
        conn.exec_driver_sql(
            "SELECT id, parent_id, name, type, perm_code, sort, is_deleted "
            "FROM sys_permission WHERE id IN (40, 50) ORDER BY id"
        ).all()
        == before_existing
    )
    assert _grant_rows(conn) == sorted(
        (1, permission_id) for permission_id in _canonical_ids(conn).values()
    )


def test_existing_export_permission_is_left_unchanged(connection):
    conn = connection
    _insert_role(conn, 1, 1)
    _insert_permission(
        conn,
        40,
        "report:export",
        name="历史报表导出",
        parent_id=999,
        sort=77,
        is_deleted=1,
    )
    before = conn.exec_driver_sql(
        "SELECT id, parent_id, name, type, perm_code, sort, is_deleted "
        "FROM sys_permission WHERE id=40"
    ).one()

    _run(conn)

    assert (
        conn.exec_driver_sql(
            "SELECT id, parent_id, name, type, perm_code, sort, is_deleted "
            "FROM sys_permission WHERE id=40"
        ).one()
        == before
    )
    assert (1, 40) in _grant_rows(conn)


def test_duplicate_codes_grant_only_lowest_id_without_deleting_history(connection):
    conn = connection
    _insert_role(conn, 1, 1)
    _insert_role(conn, 2, 2)
    for permission_id, code in (
        (30, "inv:stock:export"),
        (5, "inv:stock:export"),
        (31, "report:export"),
        (6, "report:export"),
        (32, "fin:statement:export"),
        (7, "fin:statement:export"),
    ):
        _insert_permission(conn, permission_id, code, name=f"historical-{permission_id}")
    conn.exec_driver_sql(
        "INSERT INTO sys_role_permission VALUES (1, 30), (1, 5), (2, 31)"
    )
    before_permissions = _permission_rows(conn)

    assert migration._canonical_permission_id(conn, "inv:stock:export") == 5
    assert migration._canonical_permission_id(conn, "missing:code") is None
    _run(conn)

    assert _permission_rows(conn) == before_permissions
    assert _grant_rows(conn) == [
        (1, 5),
        (1, 6),
        (1, 7),
        (1, 30),
        (2, 5),
        (2, 6),
        (2, 7),
        (2, 31),
    ]
    assert _canonical_ids(conn) == {
        "fin:statement:export": 7,
        "inv:stock:export": 5,
        "report:export": 6,
    }


def test_noop_downgrade_then_upgrade_is_idempotent_and_preserves_other_data(connection):
    conn = connection
    _insert_role(conn, 1, 1)
    _insert_permission(conn, 90, "unrelated:permission", name="保留权限")
    conn.exec_driver_sql("INSERT INTO sys_role_permission VALUES (1, 90)")
    conn.exec_driver_sql("INSERT INTO unrelated VALUES (1, 'unchanged')")

    _run(conn)
    first_permissions = _permission_rows(conn)
    first_grants = _grant_rows(conn)
    _run(conn, "downgrade")
    assert _permission_rows(conn) == first_permissions
    assert _grant_rows(conn) == first_grants

    _run(conn)

    assert _permission_rows(conn) == first_permissions
    assert _grant_rows(conn) == first_grants
    assert conn.exec_driver_sql("SELECT * FROM unrelated").all() == [(1, "unchanged")]
    assert sum(row["perm_code"] in EXPORT_CODES for row in _permission_rows(conn)) == 3
    assert len(_grant_rows(conn)) == 4
