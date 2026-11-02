"""Migration/startup safety tests; only in-memory SQLite and mocked startup I/O.

The historical PostgreSQL migration chain is intentionally not run on SQLite.
These tests execute this revision directly through Alembic Operations instead.
"""

import asyncio
import importlib.util
import logging
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateIndex

from app.db import init_db as bootstrap
from app.db.base import Base


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "f2b6c8d0e1a3_base_data_safety.py"
)
spec = importlib.util.spec_from_file_location("base_data_safety_migration", MIGRATION_PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.fixture
def old_connection():
    """Small frozen predecessor schema, not the application's current metadata."""
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        for stmt in (
            "CREATE TABLE bd_goods_category (id INTEGER PRIMARY KEY, tenant_id BIGINT NOT NULL, "
            "name TEXT NOT NULL, parent_id BIGINT, is_deleted BOOLEAN NOT NULL DEFAULT 0)",
            "CREATE UNIQUE INDEX uq_goods_category_name_tenant "
            "ON bd_goods_category (name, parent_id, tenant_id)",
            "CREATE TABLE bd_unit (id INTEGER PRIMARY KEY, tenant_id BIGINT NOT NULL, "
            "precision INTEGER NOT NULL)",
            "CREATE TABLE bd_goods (id INTEGER PRIMARY KEY, tenant_id BIGINT NOT NULL, "
            "barcode TEXT, is_deleted BOOLEAN NOT NULL DEFAULT 0, "
            "category_id BIGINT, unit_id BIGINT, "
            "cost_price NUMERIC(18,2) NOT NULL DEFAULT 0, "
            "sale_price NUMERIC(18,2) NOT NULL DEFAULT 0, "
            "stock_lower NUMERIC(18,4) NOT NULL DEFAULT 0, "
            "stock_upper NUMERIC(18,4) NOT NULL DEFAULT 0)",
            "CREATE TABLE bd_customer (id INTEGER PRIMARY KEY, tenant_id BIGINT NOT NULL, "
            "credit_limit NUMERIC(18,2) NOT NULL DEFAULT 0)",
            "CREATE TABLE sys_user (id INTEGER PRIMARY KEY, tenant_id BIGINT NOT NULL)",
            "CREATE TABLE sys_oper_log (id INTEGER PRIMARY KEY, user_id BIGINT, params TEXT)",
            "CREATE TABLE sys_task (id INTEGER PRIMARY KEY, created_by BIGINT, params TEXT)",
        ):
            conn.exec_driver_sql(stmt)
        yield conn
    engine.dispose()


def _run(conn, direction="upgrade"):
    with Operations.context(MigrationContext.configure(conn)):
        getattr(migration, direction)()


def _snapshot(conn):
    return {
        name: list(conn.exec_driver_sql(f'SELECT * FROM "{name}" ORDER BY id'))
        for name in sa.inspect(conn).get_table_names()
    }


def test_upgrade_downgrade_preserves_data_and_unknown_ownership(old_connection):
    conn = old_connection
    conn.exec_driver_sql("INSERT INTO sys_user VALUES (1, 22)")
    conn.exec_driver_sql("INSERT INTO sys_oper_log VALUES (10, 1, 'private-log')")
    conn.exec_driver_sql("INSERT INTO sys_task VALUES (20, 1, 'private-task')")
    conn.exec_driver_sql("INSERT INTO bd_goods (id, tenant_id, barcode) VALUES (1, 1, 'unchanged')")
    conn.exec_driver_sql(
        "INSERT INTO bd_goods_category (id, tenant_id, name) VALUES (1, 1, 'unchanged')"
    )
    before = _snapshot(conn)
    _run(conn)
    for table, row_id in (("sys_oper_log", 10), ("sys_task", 20)):
        column = next(c for c in sa.inspect(conn).get_columns(table) if c["name"] == "tenant_id")
        assert column["nullable"] is True
        assert column["default"] is None
        assert isinstance(column["type"], sa.Integer)
        assert (
            conn.exec_driver_sql(f"SELECT tenant_id FROM {table} WHERE id={row_id}").scalar()
            is None
        )
        conn.exec_driver_sql(f"INSERT INTO {table} (id) VALUES ({row_id + 1})")
        assert (
            conn.exec_driver_sql(f"SELECT tenant_id FROM {table} WHERE id={row_id + 1}").scalar()
            is None
        )
        conn.exec_driver_sql(f"DELETE FROM {table} WHERE id={row_id + 1}")
        index = next(
            i for i in sa.inspect(conn).get_indexes(table) if i["name"] == f"ix_{table}_tenant_id"
        )
        assert index["column_names"] == ["tenant_id"]
        assert not index["unique"]
    assert conn.exec_driver_sql("SELECT barcode FROM bd_goods").scalar() == "unchanged"
    assert conn.exec_driver_sql("SELECT name FROM bd_goods_category").scalar() == "unchanged"
    _run(conn, "downgrade")
    assert _snapshot(conn) == before
    assert [i["name"] for i in sa.inspect(conn).get_indexes("bd_goods_category")] == [
        "uq_goods_category_name_tenant"
    ]
    _run(conn)
    assert conn.exec_driver_sql("SELECT tenant_id FROM sys_oper_log").scalar() is None


def test_indexes_match_current_models(old_connection):
    _run(old_connection)
    for table_name, index_name in (
        ("bd_goods", "uq_goods_barcode_active_tenant"),
        ("bd_goods_category", "uq_goods_category_root_name_tenant"),
        ("sys_oper_log", "ix_sys_oper_log_tenant_id"),
        ("sys_task", "ix_sys_task_tenant_id"),
    ):
        model_index = next(
            i for i in Base.metadata.tables[table_name].indexes if i.name == index_name
        )
        expected = str(CreateIndex(model_index).compile(dialect=old_connection.dialect))
        actual = old_connection.execute(
            sa.text("SELECT sql FROM sqlite_master WHERE type='index' AND name=:name"),
            {"name": index_name},
        ).scalar_one()
        assert actual == expected


def test_unique_indexes_have_correct_scope(old_connection):
    conn = old_connection
    _run(conn)
    conn.exec_driver_sql(
        "INSERT INTO bd_goods (id, tenant_id, barcode, is_deleted) VALUES "
        "(1, 1, 'A', 0), (2, 2, 'A', 0), (3, 1, 'A', 1), (4, 1, 'A', 1), "
        "(5, 1, '', 0), (6, 1, '', 0), (7, 1, NULL, 0), (8, 1, NULL, 0)"
    )
    with pytest.raises(sa.exc.IntegrityError):
        conn.exec_driver_sql("INSERT INTO bd_goods (id, tenant_id, barcode) VALUES (9, 1, 'A')")
    with pytest.raises(sa.exc.IntegrityError):
        conn.exec_driver_sql("UPDATE bd_goods SET is_deleted=0 WHERE id=3")
    conn.exec_driver_sql(
        "INSERT INTO bd_goods_category (id, tenant_id, name, parent_id) VALUES "
        "(1, 1, 'root', NULL), (2, 2, 'root', NULL), (3, 1, 'root', 1)"
    )
    with pytest.raises(sa.exc.IntegrityError):
        conn.exec_driver_sql(
            "INSERT INTO bd_goods_category (id, tenant_id, name, is_deleted) "
            "VALUES (4, 1, 'root', 1)"
        )
    with pytest.raises(sa.exc.IntegrityError):
        conn.exec_driver_sql(
            "INSERT INTO bd_goods_category (id, tenant_id, name, parent_id) "
            "VALUES (5, 1, 'root', 1)"
        )


@pytest.mark.parametrize(
    "table,index_name,first,second",
    [
        ("bd_goods", "uq_goods_barcode_active_tenant", "private-barcode", "private-barcode"),
        (
            "bd_goods",
            "uq_goods_barcode_active_tenant",
            "private-barcode",
            " \tprivate-barcode\u3000",
        ),
        ("bd_goods", "uq_goods_barcode_active_tenant", " \t", " \t"),
        (
            "bd_goods_category",
            "uq_goods_category_root_name_tenant",
            "private-category",
            "private-category",
        ),
        (
            "bd_goods_category",
            "uq_goods_category_root_name_tenant",
            "private-category",
            " private-category ",
        ),
    ],
)
def test_duplicate_preflight_fails_before_any_changes(
    old_connection, table, index_name, first, second
):
    conn = old_connection
    column = "barcode" if table == "bd_goods" else "name"
    for row_id, value in ((101, first), (202, second)):
        conn.execute(
            sa.text(f"INSERT INTO {table} (id, tenant_id, {column}) VALUES (:id, 1, :value)"),
            {"id": row_id, "value": value},
        )
    before = _snapshot(conn)
    with pytest.raises(RuntimeError) as caught:
        _run(conn)
    message = str(caught.value)
    assert index_name in message
    assert "ids=[101, 202]" in message
    assert "private-barcode" not in message and "private-category" not in message
    assert _snapshot(conn) == before
    assert "tenant_id" not in {c["name"] for c in sa.inspect(conn).get_columns("sys_oper_log")}
    assert not any(i["name"] == index_name for i in sa.inspect(conn).get_indexes(table))


@pytest.mark.parametrize("value", [" private-barcode ", "\tprivate-barcode\u3000", " \t\u3000"])
def test_single_non_normalized_barcode_blocks_without_changing_data(old_connection, value):
    conn = old_connection
    conn.execute(
        sa.text("INSERT INTO bd_goods (id, tenant_id, barcode) VALUES (101, 1, :value)"),
        {"value": value},
    )
    before = _snapshot(conn)
    with pytest.raises(
        RuntimeError, match=r"non-normalized bd_goods.barcode: ids=\[101\]"
    ) as caught:
        _run(conn)
    assert "private-barcode" not in str(caught.value)
    assert _snapshot(conn) == before
    assert "tenant_id" not in {c["name"] for c in sa.inspect(conn).get_columns("sys_oper_log")}
    assert not sa.inspect(conn).get_indexes("bd_goods")


@pytest.mark.parametrize(
    "parent_id,is_deleted", [(None, False), (1, False), (None, True), (1, True)]
)
def test_non_normalized_category_at_every_level_blocks(old_connection, parent_id, is_deleted):
    conn = old_connection
    conn.execute(
        sa.text(
            "INSERT INTO bd_goods_category (id, tenant_id, name, parent_id, is_deleted) "
            "VALUES (101, 1, :value, :parent, :deleted)"
        ),
        {"value": " private-category ", "parent": parent_id, "deleted": is_deleted},
    )
    before = _snapshot(conn)
    with pytest.raises(RuntimeError, match="non-normalized bd_goods_category.name") as caught:
        _run(conn)
    assert "ids=[101]" in str(caught.value)
    assert "private-category" not in str(caught.value)
    assert _snapshot(conn) == before
    assert "tenant_id" not in {c["name"] for c in sa.inspect(conn).get_columns("sys_task")}
    assert [i["name"] for i in sa.inspect(conn).get_indexes("bd_goods_category")] == [
        "uq_goods_category_name_tenant"
    ]


def test_normalized_duplicates_exclude_deleted_goods_and_other_tenants(old_connection):
    old_connection.exec_driver_sql(
        "INSERT INTO bd_goods (id, tenant_id, barcode, is_deleted) VALUES "
        "(1, 1, 'A', 0), (2, 2, 'A', 0), (3, 1, ' A ', 1)"
    )
    _run(old_connection)
    assert (
        old_connection.exec_driver_sql("SELECT barcode FROM bd_goods WHERE id=3").scalar() == " A "
    )


def test_preflight_reports_cross_tenant_and_numeric_anomalies_without_values(
    old_connection, caplog
):
    conn = old_connection
    conn.exec_driver_sql("INSERT INTO bd_unit VALUES (11, 1, 0), (12, 2, 2), (13, 1, 9)")
    conn.exec_driver_sql(
        "INSERT INTO bd_goods_category (id, tenant_id, name) VALUES (21, 2, 'private-category')"
    )
    conn.exec_driver_sql(
        "INSERT INTO bd_goods "
        "(id, tenant_id, barcode, unit_id, category_id, cost_price, stock_lower) "
        "VALUES (31, 1, 'private-barcode', 12, 21, 1.234, 0), (32, 1, NULL, 11, NULL, 0, 0.5)"
    )
    conn.exec_driver_sql("INSERT INTO bd_customer VALUES (41, 1, -123.45)")
    before = _snapshot(conn)
    with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
        _run(conn)
    assert "cross-tenant reference bd_goods.unit_id" in caplog.text
    assert "cross-tenant reference bd_goods.category_id" in caplog.text
    assert "bd_goods.cost_price; ids=[31]" in caplog.text
    assert "bd_goods.stock_lower; ids=[32]" in caplog.text
    assert "bd_unit.precision; ids=[13]" in caplog.text
    assert "bd_customer.credit_limit; ids=[41]" in caplog.text
    assert "private-barcode" not in caplog.text and "private-category" not in caplog.text
    assert "1.234" not in caplog.text and "123.45" not in caplog.text
    _run(conn, "downgrade")
    assert _snapshot(conn) == before


@pytest.mark.parametrize(
    "value,scale,digits,invalid",
    [
        ("1.2300", 2, 16, False),
        ("0.0000", 0, 14, False),
        ("1.234", 2, 16, True),
        ("0.00001", 4, 14, True),
        ("NaN", 2, 16, True),
        ("Infinity", 2, 16, True),
        ("-1", 2, 16, True),
        ("10000000000000000", 2, 16, True),
        ("9999999999999999.99", 2, 16, False),
    ],
)
def test_numeric_preflight_does_not_round(value, scale, digits, invalid):
    assert migration._invalid_number(value, scale, digits) is invalid


def _forbid_schema_shortcuts(monkeypatch):
    create = Mock(side_effect=AssertionError("must not create_all"))
    stamp = Mock(side_effect=AssertionError("must not stamp"))
    monkeypatch.setattr(Base.metadata, "create_all", create)
    monkeypatch.setattr(bootstrap.command, "stamp", stamp)
    monkeypatch.setattr(MigrationContext, "stamp", stamp)
    return create, stamp


@pytest.mark.parametrize("schema", ["old", "unmanaged", "empty_version", "multiple"])
def test_init_db_rejects_old_or_unmanaged_database_without_stamp(monkeypatch, schema):
    _forbid_schema_shortcuts(monkeypatch)
    seed = AsyncMock()
    upgrade = Mock(side_effect=AssertionError("old databases require explicit migration"))
    monkeypatch.setattr(bootstrap, "_seed", seed)
    monkeypatch.setattr(bootstrap, "_upgrade_to_head", upgrade)
    monkeypatch.setattr(bootstrap, "_alembic_head", lambda: migration.revision)

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        monkeypatch.setattr(bootstrap, "engine", engine)
        try:
            async with engine.begin() as conn:
                await conn.execute(sa.text("CREATE TABLE legacy_record (id INTEGER PRIMARY KEY)"))
                await conn.execute(sa.text("INSERT INTO legacy_record VALUES (7)"))
                if schema != "unmanaged":
                    await conn.execute(sa.text("CREATE TABLE alembic_version (version_num TEXT)"))
                    if schema != "empty_version":
                        await conn.execute(
                            sa.text("INSERT INTO alembic_version VALUES ('e4a8b7c6d5f4')")
                        )
                    if schema == "multiple":
                        await conn.execute(sa.text("INSERT INTO alembic_version VALUES ('other')"))
            with pytest.raises(RuntimeError, match="alembic upgrade head"):
                await bootstrap.init_db()
            async with engine.connect() as conn:
                assert (await conn.execute(sa.text("SELECT id FROM legacy_record"))).scalar() == 7
                if schema != "unmanaged":
                    rows = (
                        (await conn.execute(sa.text("SELECT version_num FROM alembic_version")))
                        .scalars()
                        .all()
                    )
                    assert migration.revision not in rows
            seed.assert_not_awaited()
            upgrade.assert_not_called()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_empty_database_migration_failure_never_stamps(monkeypatch):
    _forbid_schema_shortcuts(monkeypatch)
    monkeypatch.setattr(bootstrap, "_alembic_head", lambda: migration.revision)
    upgrade = Mock(side_effect=RuntimeError("synthetic migration failure"))
    monkeypatch.setattr(bootstrap.command, "upgrade", upgrade)
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            with pytest.raises(RuntimeError, match="alembic upgrade head"):
                bootstrap._prepare_schema(conn)
            assert sa.inspect(conn).get_table_names() == []
            assert upgrade.call_args.args[0].attributes["connection"] is conn
    finally:
        engine.dispose()


def test_empty_database_runs_real_upgrade_path_then_seed(monkeypatch):
    _forbid_schema_shortcuts(monkeypatch)
    monkeypatch.setattr(bootstrap, "_alembic_head", lambda: migration.revision)
    seed = AsyncMock()
    monkeypatch.setattr(bootstrap, "_seed", seed)
    session_context = AsyncMock()
    monkeypatch.setattr(bootstrap, "AsyncSessionLocal", Mock(return_value=session_context))

    def migrated(cfg, target):
        assert target == "head"
        connection = cfg.attributes["connection"]
        assert sa.inspect(connection).get_table_names() == []
        connection.exec_driver_sql("CREATE TABLE alembic_version (version_num TEXT)")
        connection.execute(
            sa.text("INSERT INTO alembic_version VALUES (:revision)"),
            {"revision": migration.revision},
        )

    upgrade = Mock(side_effect=migrated)
    monkeypatch.setattr(bootstrap.command, "upgrade", upgrade)

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        monkeypatch.setattr(bootstrap, "engine", engine)
        try:
            await bootstrap.init_db()
            upgrade.assert_called_once()
            seed.assert_awaited_once_with(session_context.__aenter__.return_value)
            # Already at head: no create_all, stamp, or second upgrade.
            await bootstrap.init_db()
            upgrade.assert_called_once()
            assert seed.await_count == 2
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_success_without_revision_is_not_treated_as_migrated(monkeypatch):
    _forbid_schema_shortcuts(monkeypatch)
    monkeypatch.setattr(bootstrap, "_alembic_head", lambda: migration.revision)
    monkeypatch.setattr(bootstrap.command, "upgrade", Mock())
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn, pytest.raises(RuntimeError, match="alembic upgrade head"):
            bootstrap._prepare_schema(conn)
    finally:
        engine.dispose()


def test_head_lookup_is_independent_of_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert bootstrap._alembic_head() == migration.revision
    assert migration.down_revision == "e4a8b7c6d5f4"


def test_missing_migration_scripts_are_fatal(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "ALEMBIC_INI", tmp_path / "missing.ini")
    with pytest.raises(RuntimeError, match="Alembic head"):
        bootstrap._alembic_head()
