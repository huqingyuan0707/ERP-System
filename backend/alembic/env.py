"""Alembic 异步迁移环境。

连接串统一从 app.core.config.settings 注入，禁止在 alembic.ini 中写死生产库地址。
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.db.base import Base  # 导入后 Base.metadata 已包含全部模型

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_object(object_, name, type_, reflected, compare_to) -> bool:  # noqa: ANN001
    """过滤历史归档表，使 alembic check 不把它们当成待删除对象。

    7f496af09f7f 把旧财务表重命名为 fin_legacy_* 保留审计痕迹，这些表不在模型中，
    若不过滤，autogenerate 会判定「库里有、模型没有」并要求生成 drop_table。
    """
    if type_ == "table" and name.startswith("fin_legacy_"):
        return False
    return True


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL，不连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # 本地迁移验证用：MIGRATION_SCHEMA 指定独立 schema，把迁移产物与开发库隔离开，
    # 避免在开发库上反复升级/降级。asyncpg 的 DSN 不接受 options 参数，
    # 因此只能用 server_settings 传 search_path。
    schema = os.getenv("MIGRATION_SCHEMA")
    connect_args = {"server_settings": {"search_path": schema}} if schema else {}
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
        # 迁移 DDL 由 SQLAlchemy 的 autobegin 事务承载，连接上下文退出时会回滚，
        # 必须显式提交，否则迁移日志显示成功但表与 alembic_version 都不会落库。
        await connection.commit()
    await connectable.dispose()


def run_migrations_online() -> None:
    # 若调用方（如 app/db/init_db.py）通过 config.attributes["connection"] 传入了
    # 已建立的连接，则复用它执行迁移，保证迁移与调用方处于同一事务/同一连接，
    # 迁移产生的种子数据对调用方立即可见。
    # 未传入时保持原行为：自行创建引擎连接。
    external_connection = config.attributes.get("connection")
    if external_connection is not None:
        do_run_migrations(external_connection)
        return
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
