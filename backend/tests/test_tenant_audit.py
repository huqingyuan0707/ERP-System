"""真实租户依赖和操作日志隔离回归，不覆盖 get_current_tenant。"""

import asyncio
import json
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.deps import TenantDep, get_current_user
from app.core.exceptions import register_exception_handlers
from app.db.base import Base
from app.models.sys import SysOperLog
from app.repositories.oper_log import OperLogRepository
from app.services.audit import AuditMiddleware, bind_audit_actor, write_oper_log


def test_tenant_comes_from_authenticated_user():
    async def run():
        app = FastAPI()
        register_exception_handlers(app)

        async def user():
            return SimpleNamespace(id=11, tenant_id=1)

        app.dependency_overrides[get_current_user] = user

        @app.get("/tenant")
        async def tenant(tenant_id: TenantDep):
            return {"tenant": tenant_id}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for value, expected in (
                (None, 200),
                ("1", 200),
                ("2", 403),
                ("invalid", 400),
                ("0", 400),
                ("-1", 400),
                ("9223372036854775808", 400),
            ):
                headers = {"X-Tenant-Id": value} if value is not None else {}
                response = await client.get("/tenant", headers=headers)
                assert response.status_code == expected
                if expected == 200:
                    assert response.json() == {"tenant": 1}

    asyncio.run(run())


def test_operation_logs_are_scoped_and_redacted():
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        app = FastAPI()
        app.add_middleware(AuditMiddleware)

        @app.post("/base/example")
        async def operation():
            bind_audit_actor(1, 11)
            async with sessions() as db:
                await write_oper_log(
                    db, tenant_id=1, operator_id=11, action="新增商品", params={"goodsId": 1}
                )
                await db.commit()
            return {"ok": True}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/base/example", json={"password": "never-log-me", "name": "visible"}
            )
            assert response.status_code == 200
        async with sessions() as db:
            db.add_all(
                [
                    SysOperLog(tenant_id=2, user_id=22, module="基础资料", action="其他租户"),
                    SysOperLog(tenant_id=None, user_id=None, module="基础资料", action="归属未知"),
                ]
            )
            await db.commit()
            rows, total = await OperLogRepository(db, tenant_id=1).list_with_filter(
                None, None, None, None, None, None, 1, 20
            )
            assert total == 1
            log = rows[0]
            assert log.tenant_id == 1 and log.user_id == 11
            assert log.method == "POST" and log.url == "/base/example"
            assert log.duration_ms >= 0
            assert json.loads(log.params)["body"]["password"] == "***"
            assert "never-log-me" not in log.params
        await engine.dispose()

    asyncio.run(run())


def test_failed_write_is_audited_after_rollback(monkeypatch):
    async def run():
        import app.db.session as session_module

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(session_module, "AsyncSessionLocal", sessions)
        app = FastAPI()
        app.add_middleware(AuditMiddleware)
        register_exception_handlers(app)

        @app.post("/base/example")
        async def operation():
            from app.core.exceptions import AppException, ErrorCode

            bind_audit_actor(1, 11)
            async with sessions() as db:
                await write_oper_log(db, tenant_id=1, operator_id=11, action="不应提交的成功")
                await db.rollback()
            raise AppException(ErrorCode.CONFLICT, "冲突")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/base/example", json={"token": "private-token"})
            assert response.status_code == 409
        async with sessions() as db:
            logs = list((await db.execute(select(SysOperLog))).scalars())
            assert len(logs) == 1
            assert logs[0].status == 0 and logs[0].tenant_id == 1
            assert logs[0].result == "HTTP 409"
            assert "private-token" not in logs[0].params
        await engine.dispose()

    asyncio.run(run())
