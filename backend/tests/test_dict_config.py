"""C-05 验收测试：字典缓存与失效、参数默认值、单号并发唯一、非法规则 422。"""

import asyncio
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import httpx
from sqlalchemy import delete, select

from app.core.cache import clear_memory_cache
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.sys import SysBillNoSeq, SysConfig, SysDictItem, SysDictType
from app.utils.bill_no import generate_bill_no


API = "/api/v1"
ADMIN = ("admin", "admin123")


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


@asynccontextmanager
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _admin_login() -> str:
    async def _login() -> str:
        async with _client() as client:
            resp = await client.post(
                f"{API}/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]}
            )
            assert resp.status_code == 200, resp.text
            return str(resp.json()["data"]["accessToken"])

    return asyncio.run(_login())


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_dict_grouped_and_cache_invalidate() -> None:
    """字典分组查询；修改字典项后缓存失效、新值立即可见。"""
    clear_memory_cache()
    token = _admin_login()

    async def _assert() -> None:
        async with _client() as client:
            resp = await client.get(f"{API}/sys/dicts", headers=_headers(token))
            assert resp.status_code == 200, resp.text
            grouped = resp.json()["data"]
            assert set(grouped) >= {"yes_no", "invoice_type", "settlement_type"}
            assert grouped["yes_no"] == [
                {"label": "是", "value": "1"},
                {"label": "否", "value": "0"},
            ]

            # 找到「是」字典项并修改 label，验证缓存失效后可见新值
            async with AsyncSessionLocal() as session:
                item_id = await session.scalar(
                    select(SysDictItem.id).where(
                        SysDictItem.type_code == "yes_no", SysDictItem.value == "1"
                    )
                )
                assert item_id is not None
            resp = await client.put(
                f"{API}/sys/dicts/items/{item_id}",
                json={"label": "是对的"},
                headers=_headers(token),
            )
            assert resp.status_code == 200, resp.text
            resp = await client.get(f"{API}/sys/dicts", headers=_headers(token))
            labels = [item["label"] for item in resp.json()["data"]["yes_no"]]
            assert labels == ["是对的", "否"]

            # 还原
            resp = await client.put(
                f"{API}/sys/dicts/items/{item_id}",
                json={"label": "是"},
                headers=_headers(token),
            )
            assert resp.status_code == 200, resp.text

    _run(_assert())


def test_dict_type_crud_conflict() -> None:
    """字典类型新增/冲突/删除。"""
    clear_memory_cache()
    token = _admin_login()
    code = "t" + "d" * 6

    async def _assert() -> None:
        async with _client() as client:
            resp = await client.post(
                f"{API}/sys/dicts/types",
                json={"code": code, "name": "测试字典"},
                headers=_headers(token),
            )
            assert resp.status_code == 200, resp.text
            type_id = resp.json()["data"]["id"]

            resp = await client.post(
                f"{API}/sys/dicts/types",
                json={"code": code, "name": "重复编码"},
                headers=_headers(token),
            )
            assert resp.status_code == 409

            resp = await client.post(
                f"{API}/sys/dicts/types/{code}/items",
                json={"label": "选项A", "value": "a"},
                headers=_headers(token),
            )
            assert resp.status_code == 200, resp.text

            resp = await client.delete(f"{API}/sys/dicts/types/{type_id}", headers=_headers(token))
            assert resp.status_code == 200, resp.text

            async with AsyncSessionLocal() as session:
                items = (
                    (
                        await session.execute(
                            select(SysDictItem).where(SysDictItem.type_code == code)
                        )
                    )
                    .scalars()
                    .all()
                )
                assert all(item.is_deleted for item in items)

            # 清理类型行
            async with AsyncSessionLocal() as session:
                await session.execute(delete(SysDictItem).where(SysDictItem.type_code == code))
                await session.execute(delete(SysDictType).where(SysDictType.code == code))
                await session.commit()

    _run(_assert())


def test_config_list_update_and_default() -> None:
    """参数列表、批量保存与缺失配置默认值。"""
    clear_memory_cache()
    token = _admin_login()

    async def _assert() -> None:
        async with _client() as client:
            resp = await client.get(f"{API}/sys/configs", headers=_headers(token))
            assert resp.status_code == 200, resp.text
            configs = {c["key"]: c["value"] for c in resp.json()["data"]}
            assert configs.get("company.name") is not None
            assert configs.get("billno.pur_order") == "PO,4"

            resp = await client.put(
                f"{API}/sys/configs",
                json={"items": [{"key": "company.name", "value": "新公司名"}]},
                headers=_headers(token),
            )
            assert resp.status_code == 200, resp.text

            resp = await client.get(f"{API}/sys/configs", headers=_headers(token))
            configs = {c["key"]: c["value"] for c in resp.json()["data"]}
            assert configs["company.name"] == "新公司名"

            # 还原默认值
            async with AsyncSessionLocal() as session:
                entity = await session.scalar(
                    select(SysConfig).where(SysConfig.key == "company.name")
                )
                assert entity is not None
                entity.value = "某某科技有限公司"
                await session.commit()

        # 缺失配置返回默认值
        async with AsyncSessionLocal() as session:
            from app.services.config import ConfigService

            value = await ConfigService(session).get_value("not.exist.key", default="fallback")
            assert value == "fallback"

    _run(_assert())


def test_billno_concurrent_unique() -> None:
    """并发生成单号全部唯一（100 个，分批并发以匹配连接池上限）。"""
    clear_memory_cache()
    biz_key = "t_test"

    async def _assert() -> None:
        async def _gen() -> str:
            async with AsyncSessionLocal() as session:
                return await generate_bill_no(session, biz_key)

        numbers: list[str] = []
        for _ in range(10):
            batch = await asyncio.gather(*(_gen() for _ in range(10)))
            numbers.extend(batch)
        assert len(numbers) == 100
        assert len(numbers) == len(set(numbers)), "单号存在重复"
        assert all(n.startswith("T_TEST") for n in numbers)

    _run(_assert())

    async def _cleanup() -> None:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(SysBillNoSeq).where(SysBillNoSeq.biz_key == biz_key))
            await session.commit()

    _run(_cleanup())


def test_billno_rule_validation() -> None:
    """非法单号规则保存返回 422：缺前缀、宽度越界。"""
    clear_memory_cache()
    token = _admin_login()

    async def _assert() -> None:
        async with _client() as client:
            for bad_value in (",4", "X,9", "X,0", "X,abc"):
                resp = await client.put(
                    f"{API}/sys/configs",
                    json={"items": [{"key": "billno.t_bad", "value": bad_value}]},
                    headers=_headers(token),
                )
                assert resp.status_code == 422, f"{bad_value}: {resp.text}"

            # 合法规则可保存（更新既有参数后还原）
            resp = await client.put(
                f"{API}/sys/configs",
                json={"items": [{"key": "billno.pur_order", "value": "PO,6"}]},
                headers=_headers(token),
            )
            assert resp.status_code == 200, resp.text
            resp = await client.put(
                f"{API}/sys/configs",
                json={"items": [{"key": "billno.pur_order", "value": "PO,4"}]},
                headers=_headers(token),
            )
            assert resp.status_code == 200, resp.text

    _run(_assert())

    async def _cleanup() -> None:
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(SysBillNoSeq).where(SysBillNoSeq.biz_key == "t_bad")
            )
            await session.commit()

    _run(_cleanup())
