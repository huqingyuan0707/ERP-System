"""本地迁移链验证：在独立 schema 上跑 upgrade → downgrade → upgrade。

用途：CI 的 db-migration job 只在干净 PostgreSQL 库上验证迁移链，而本地开发库
已经被反复升级/降级过，结构与迁移链并不同步（alembic check 会报出存量漂移）。
本脚本通过 backend/alembic/env.py 支持的 MIGRATION_SCHEMA 环境变量，把迁移产物
隔离到临时 schema 中，从而在本地复现「干净库全链升级」并验证可回滚。

用法：
    python scripts/dev/verify_migrations.py

前置条件：
    1. 本地 PostgreSQL 可用，且 backend/.env 里的 DATABASE_URL 能连通；
    2. 该账号具备 CREATE SCHEMA 权限（本项目用的 erp 账号具备）；
    3. 依赖已安装：pip install -r backend/requirements.txt
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# 仓库根目录：本文件位于 scripts/dev/ 下，往上两级才是仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
ENV_FILE = BACKEND_DIR / ".env"


def alembic_config() -> Config:
    """构造指向 backend/alembic 的 Alembic 配置。

    alembic.ini 里的 script_location 是相对路径，直接读配置会按当前工作目录解析，
    这里改成绝对路径，保证脚本从任意目录执行都能找到迁移脚本。
    """
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return config


def read_database_url() -> str:
    """从 backend/.env 读取 DATABASE_URL。

    密码只用于建连，不会打印到日志，避免凭据进入终端历史或 CI 日志。
    """
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1]
    raise RuntimeError(f"backend/.env 中没有 DATABASE_URL，无法验证迁移")


def head_revision() -> str:
    """取迁移链的当前 head 版本号。"""
    script = ScriptDirectory.from_config(alembic_config())
    head = script.get_current_head()
    if head is None:
        raise RuntimeError("迁移链里没有 head 版本")
    return head


def merge_parents() -> tuple[str, ...]:
    """取 head 的父版本列表。

    head 是 merge point（两个父分支）时，`alembic downgrade -1` 无法判断回退方向，
    会报 "Ambiguous walk"，必须显式指定要回到的父版本。
    """
    script = ScriptDirectory.from_config(alembic_config())
    revision = script.get_revision(head_revision())
    parents = revision.down_revision
    if isinstance(parents, str):
        return (parents,)
    return tuple(parents)


def run_alembic(schema: str, *args: str) -> None:
    """在指定 schema 环境下执行 alembic 命令，失败直接抛错终止。"""
    env = dict(os.environ)
    env["MIGRATION_SCHEMA"] = schema  # env.py 用它把迁移产物隔离到独立 schema
    completed = subprocess.run(  # noqa: S603 - 参数固定，无 shell 注入
        [sys.executable, "-m", "alembic", *args],
        cwd=str(BACKEND_DIR),
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"alembic {' '.join(args)} 失败，退出码 {completed.returncode}")


async def create_schema(url: str, schema: str) -> None:
    """创建验证用 schema。

    走 SQLAlchemy 引擎而不是裸 asyncpg，避免连接串驱动后缀（postgresql+asyncpg://）
    需要单独转换，也与 alembic 使用同一套连接配置。
    """
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    finally:
        await engine.dispose()


async def drop_schema(url: str, schema: str) -> None:
    """清理验证用 schema（连同其中的对象）。"""
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    finally:
        await engine.dispose()


async def inspect_schema(url: str, schema: str) -> tuple[int, str]:
    """返回该 schema 下的表数量与 alembic_version 记录。"""
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with engine.connect() as connection:
            table_count = await connection.scalar(
                text("SELECT count(*) FROM information_schema.tables WHERE table_schema = :schema"),
                {"schema": schema},
            )
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            return int(table_count or 0), str(revision or "")
    finally:
        await engine.dispose()


async def main() -> int:
    """执行验证流程：全链升级 → 回滚 → 再升级，最后清理临时 schema。"""
    url = read_database_url()
    schema = f"mig_check_{uuid.uuid4().hex[:8]}"
    head = head_revision()
    parents = merge_parents()
    target = parents[0]
    print(f"验证 schema: {schema}")
    print(f"head: {head}；回退目标（父版本）: {target}")

    await create_schema(url, schema)
    try:
        # 1. 干净 schema 上跑完整迁移链
        run_alembic(schema, "upgrade", "head")
        table_count, revision = await inspect_schema(url, schema)
        print(f"[1/3] upgrade head -> 表 {table_count} 张，版本 {revision}")
        if table_count == 0:
            raise RuntimeError("迁移后没有任何表，说明迁移事务没有提交")
        if revision != head:
            raise RuntimeError(f"迁移后版本为 {revision}，期望 {head}")

        # 2. 回滚到父版本，验证可逆
        run_alembic(schema, "downgrade", target)
        _, revision_after_down = await inspect_schema(url, schema)
        print(f"[2/3] downgrade {target} -> 版本 {revision_after_down}")
        if revision_after_down == head:
            raise RuntimeError("降级后版本没有变化，回滚未生效")

        # 3. 再升级回 head
        run_alembic(schema, "upgrade", "head")
        _, revision_after_up = await inspect_schema(url, schema)
        print(f"[3/3] upgrade head -> 版本 {revision_after_up}")
        if revision_after_up != head:
            raise RuntimeError(f"重新升级后版本为 {revision_after_up}，期望 {head}")
    except Exception as exc:  # noqa: BLE001 - 顶层入口需要把失败原因完整打印出来
        print(f"验证失败：{exc}")
        return 1
    finally:
        await drop_schema(url, schema)
        print(f"已清理 schema {schema}")

    print("迁移链验证通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
