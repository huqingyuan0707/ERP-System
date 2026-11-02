"""Inventory constraint migration tests against an isolated SQLite predecessor table."""

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
    / "e8f1c2d3a4b5_inventory_stock_constraints.py"
)
spec = importlib.util.spec_from_file_location("inventory_stock_constraints", MIGRATION_PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.fixture
def stock_connection():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE inv_stock ("
            "id INTEGER PRIMARY KEY, "
            "qty NUMERIC(18,4) NOT NULL, "
            "locked_qty NUMERIC(18,4) NOT NULL, "
            "available_qty NUMERIC(18,4) NOT NULL, "
            "avg_cost NUMERIC(18,4) NOT NULL, "
            "is_deleted BOOLEAN NOT NULL DEFAULT 0)"
        )
        yield conn
    engine.dispose()


def _run(conn, direction="upgrade"):
    with Operations.context(MigrationContext.configure(conn)):
        getattr(migration, direction)()


def _constraint_names(conn):
    return {item["name"] for item in sa.inspect(conn).get_check_constraints("inv_stock")}


def test_inventory_constraint_preflight_blocks_all_rows_before_ddl(stock_connection):
    stock_connection.exec_driver_sql(
        "INSERT INTO inv_stock VALUES "
        "(1, 1, 0, 1, 0, 0), "
        "(2, 1, 0, 2, 0, 1), "
        "(3, 1, 0, 1, -1, 0)"
    )
    before = list(stock_connection.exec_driver_sql("SELECT * FROM inv_stock ORDER BY id"))

    with pytest.raises(RuntimeError, match=r"ids=\[2, 3\]"):
        _run(stock_connection)

    assert list(stock_connection.exec_driver_sql("SELECT * FROM inv_stock ORDER BY id")) == before
    assert _constraint_names(stock_connection) == set()


def test_inventory_constraints_accept_boundaries_enforce_sql_and_downgrade(stock_connection):
    stock_connection.exec_driver_sql(
        "INSERT INTO inv_stock VALUES "
        "(1, 0, 0, 0, 0, 0), "
        "(2, 10, 10, 0, 3.5, 1)"
    )
    _run(stock_connection)
    assert _constraint_names(stock_connection) == {name for name, _ in migration._CONSTRAINTS}

    invalid_rows = (
        (3, -1, 0, -1, 0, 0),
        (4, 1, -1, 2, 0, 0),
        (5, 1, 0, -1, 0, 0),
        (6, 1, 0, 0, 0, 0),
        (7, 1, 0, 1, -1, 0),
    )
    for row in invalid_rows:
        placeholders = ", ".join("?" for _ in row)
        with pytest.raises(sa.exc.IntegrityError):
            stock_connection.exec_driver_sql(
                f"INSERT INTO inv_stock VALUES ({placeholders})", row
            )

    _run(stock_connection, "downgrade")
    assert _constraint_names(stock_connection) == set()
    stock_connection.exec_driver_sql("INSERT INTO inv_stock VALUES (8, -1, 0, -1, -1, 0)")


def test_inventory_constraint_revision_is_based_on_confirmed_head():
    assert migration.down_revision == "d4e9a1b7c3f5"
