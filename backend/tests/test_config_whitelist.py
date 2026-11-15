"""S-03 系统参数白名单与敏感配置保护验收。

对应 需求条目拆分.md:298/304：
  - 白名单键；敏感配置（JWT_SECRET、数据库密码）不允许页面修改；
  - 敏感配置不出现在接口响应；操作日志记录修改前后值。
"""

import asyncio
import json
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import httpx
from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.sys import SysConfig, SysOperLog

API = "/api/v1"
SENSITIVE_KEY = "security.jwt_secret"


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


@asynccontextmanager
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        f"{API}/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["data"]["accessToken"])


def test_sensitive_config_is_hidden_and_read_only() -> None:
    """白名单外的敏感配置不出现在列表响应，PUT 返回 403 且值不变。"""

    async def scenario() -> None:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(SysConfig).where(SysConfig.key == SENSITIVE_KEY))
            session.add(SysConfig(key=SENSITIVE_KEY, value="super-secret", name="JWT 密钥"))
            await session.commit()
        try:
            async with _client() as client:
                headers = _auth(await _login(client))
                listed = await client.get(f"{API}/sys/configs", headers=headers)
                assert listed.status_code == 200, listed.text
                keys = [row["key"] for row in listed.json()["data"]]
                assert SENSITIVE_KEY not in keys, keys
                assert "company.name" in keys and "billno.pur_order" in keys

                denied = await client.put(
                    f"{API}/sys/configs",
                    json={"items": [{"key": SENSITIVE_KEY, "value": "hacked"}]},
                    headers=headers,
                )
                assert denied.status_code == 403, denied.text
                async with AsyncSessionLocal() as session:
                    value = await session.scalar(
                        select(SysConfig.value).where(SysConfig.key == SENSITIVE_KEY)
                    )
                assert value == "super-secret", "白名单外的配置不应被修改"
        finally:
            async with AsyncSessionLocal() as session:
                await session.execute(delete(SysConfig).where(SysConfig.key == SENSITIVE_KEY))
                await session.commit()

    _run(scenario())


def test_config_update_logs_before_and_after_values() -> None:
    """修改白名单参数时，操作日志与业务同事务记录 old/new。"""

    async def scenario() -> None:
        async with _client() as client:
            headers = _auth(await _login(client))
            async with AsyncSessionLocal() as session:
                original = await session.scalar(
                    select(SysConfig.value).where(SysConfig.key == "company.name")
                )
            assert original is not None
            new_value = "白名单验收公司名"
            try:
                resp = await client.put(
                    f"{API}/sys/configs",
                    json={"items": [{"key": "company.name", "value": new_value}]},
                    headers=headers,
                )
                assert resp.status_code == 200, resp.text

                async with AsyncSessionLocal() as session:
                    logs = (
                        await session.scalars(
                            select(SysOperLog)
                            .where(SysOperLog.action == "修改系统参数")
                            .order_by(SysOperLog.id.desc())
                            .limit(1)
                        )
                    ).all()
                assert logs, "缺少修改系统参数的业务日志"
                detail = json.loads(logs[0].params or "{}")
                changes = detail.get("changes", [])
                assert changes and changes[0]["key"] == "company.name", detail
                assert changes[0]["old"] == original, detail
                assert changes[0]["new"] == new_value, detail
            finally:
                async with AsyncSessionLocal() as session:
                    entity = await session.scalar(
                        select(SysConfig).where(SysConfig.key == "company.name")
                    )
                    assert entity is not None
                    entity.value = original
                    await session.commit()

    _run(scenario())
