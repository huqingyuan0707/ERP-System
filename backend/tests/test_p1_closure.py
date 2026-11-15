"""P1 闭环回归：系统类写操作幂等/审计、Redis 防重失败释放、version 乐观锁。

对应修复：
  1. configs/dicts 写接口经 IdempotencyService 包装，业务与幂等记录同事务提交；
  2. AuditMiddleware 为未写业务日志的系统类写操作自动补记成功/失败日志；
  3. 用户/角色新增的 Redis 防重占位在业务失败时释放，TTL 收敛为 60 秒；
  4. P-01/SA-01 的 version 改为条件 UPDATE，回传旧版本直接 409。
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import httpx
from sqlalchemy import func, select

from app.core.deps import get_redis
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.base_data import BdGoods
from app.models.sys import SysOperLog

API = "/api/v1"
ADMIN = ("admin", "admin123")


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
    resp = await client.post(f"{API}/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["data"]["accessToken"])


class _FakeRedis:
    """记录防重占位的 TTL 与释放动作，避免测试依赖真实 Redis。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.deleted: list[str] = []
        self.ttls: list[int | None] = []

    async def set(
        self, key: str, value: str, ex: int | None = None, nx: bool = False
    ) -> bool | None:
        self.ttls.append(ex)
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.store.pop(key, None)


def test_system_write_idempotency_and_audit() -> None:
    """重放返回首次结果、同键异参 409、不带键仍可用，且成功/失败写操作都留痕。"""

    async def scenario() -> None:
        async with _client() as client:
            headers = _auth(await _login(client))
            idem_key = "p1cfg-" + uuid.uuid4().hex[:10]
            keyed = {**headers, "Idempotency-Key": idem_key}
            body = {"items": [{"key": "sales.credit.mode", "value": "warn"}]}

            first = await client.put(f"{API}/sys/configs", json=body, headers=keyed)
            assert first.status_code == 200, first.text
            replay = await client.put(f"{API}/sys/configs", json=body, headers=keyed)
            assert replay.status_code == 200, replay.text
            assert replay.json()["data"] == first.json()["data"]

            conflict = await client.put(
                f"{API}/sys/configs",
                json={"items": [{"key": "sales.credit.mode", "value": "off"}]},
                headers=keyed,
            )
            assert conflict.status_code == 409, conflict.text
            no_key = await client.put(f"{API}/sys/configs", json=body, headers=headers)
            assert no_key.status_code == 200, no_key.text

            dict_key = "p1dict-" + uuid.uuid4().hex[:10]
            dict_headers = {**headers, "Idempotency-Key": dict_key}
            dict_body = {"code": "p" + uuid.uuid4().hex[:6], "name": "P1回归", "status": 1}
            created = await client.post(
                f"{API}/sys/dicts/types", json=dict_body, headers=dict_headers
            )
            assert created.status_code == 200, created.text
            created_replay = await client.post(
                f"{API}/sys/dicts/types", json=dict_body, headers=dict_headers
            )
            assert created_replay.status_code == 200, created_replay.text
            assert created_replay.json()["data"]["id"] == created.json()["data"]["id"], (
                "重放不应重复创建字典类型"
            )
            duplicate = await client.post(
                f"{API}/sys/dicts/types", json=dict_body, headers=headers
            )
            assert duplicate.status_code == 409, duplicate.text

            async with AsyncSessionLocal() as session:
                success_count = await session.scalar(
                    select(func.count())
                    .select_from(SysOperLog)
                    .where(SysOperLog.module == "系统管理", SysOperLog.status == 1)
                )
                failure_count = await session.scalar(
                    select(func.count())
                    .select_from(SysOperLog)
                    .where(SysOperLog.module == "系统管理", SysOperLog.status == 0)
                )
            assert success_count and success_count >= 3, f"系统管理成功日志缺失: {success_count}"
            assert failure_count and failure_count >= 1, f"系统管理失败日志缺失: {failure_count}"

            await client.delete(
                f"{API}/sys/dicts/types/{created.json()['data']['id']}", headers=headers
            )

    _run(scenario())


def test_create_user_redis_lock_released_on_failure() -> None:
    """业务失败立即释放防重占位，同名账号可以立刻重试；TTL 不再是 24 小时。"""
    fake = _FakeRedis()

    async def scenario() -> None:
        async with _client() as client:
            headers = _auth(await _login(client))
            # admin 是内置账号，新增必定触发业务冲突（不会创建脏数据）
            body = {"username": "admin", "password": "Passw0rd123"}
            failed = await client.post(f"{API}/sys/users", json=body, headers=headers)
            assert failed.status_code in (400, 409), failed.text
            assert fake.ttls and all(ttl == 60 for ttl in fake.ttls), fake.ttls
            assert fake.deleted, "业务失败后必须调用 redis.delete 释放占位"
            assert fake.store == {}, "业务失败后不应残留防重占位"

            retry = await client.post(f"{API}/sys/users", json=body, headers=headers)
            assert "重复提交" not in retry.text, retry.text

    app.dependency_overrides[get_redis] = lambda: fake
    try:
        _run(scenario())
    finally:
        app.dependency_overrides.pop(get_redis, None)


def test_apply_version_conflict_returns_409() -> None:
    """回传过期 version 直接 409；匹配版本更新后版本自增，旧版本不能复用。"""

    async def scenario() -> None:
        async with _client() as client:
            headers = _auth(await _login(client))
            async with AsyncSessionLocal() as session:
                goods_id = await session.scalar(select(BdGoods.id).limit(1))
            assert goods_id is not None, "缺少种子商品"

            body = {
                "deptId": 1,
                "needDate": "2026-12-31",
                "items": [
                    {"goodsId": int(goods_id), "qty": "1", "needDate": "2026-12-31"}
                ],
            }
            created = await client.post(f"{API}/purchase/applies", json=body, headers=headers)
            assert created.status_code == 200, created.text
            apply_id = created.json()["data"]["id"]
            version = int(created.json()["data"]["version"])

            stale = await client.put(
                f"{API}/purchase/applies/{apply_id}",
                json={"remark": "stale", "version": version + 99},
                headers=headers,
            )
            assert stale.status_code == 409, stale.text
            assert "数据已被他人修改" in stale.text

            accepted = await client.put(
                f"{API}/purchase/applies/{apply_id}",
                json={"remark": "v2", "version": version},
                headers=headers,
            )
            assert accepted.status_code == 200, accepted.text
            assert int(accepted.json()["data"]["version"]) == version + 1

            reused = await client.put(
                f"{API}/purchase/applies/{apply_id}",
                json={"remark": "old", "version": version},
                headers=headers,
            )
            assert reused.status_code == 409, reused.text

            await client.delete(f"{API}/purchase/applies/{apply_id}", headers=headers)

    _run(scenario())
