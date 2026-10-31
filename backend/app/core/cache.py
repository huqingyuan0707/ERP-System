"""轻量缓存：配置 REDIS_URL 时使用 Redis，否则退化为进程内 TTL 缓存。

字典按 ``dict:{type_code}``、参数按 ``cfg:{key}`` 分键；维护更新后调用
``cache_delete``/``cache_delete_prefix`` 失效对应键。
"""

import logging
import time
from typing import Any

from app.core.config import settings


logger = logging.getLogger("app.cache")

DEFAULT_TTL = 300

# 进程内缓存：key -> (过期时间戳, 值)
_memory_store: dict[str, tuple[float, str]] = {}

_redis_client: Any = None
_redis_checked = False


async def _get_redis() -> Any | None:
    """懒加载 Redis 客户端；未配置或连接失败时返回 None（退化为内存缓存）。"""
    global _redis_client, _redis_checked
    if not settings.REDIS_URL:
        return None
    if not _redis_checked:
        _redis_checked = True
        try:
            from redis.asyncio import Redis

            _redis_client = Redis.from_url(
                settings.REDIS_URL, socket_timeout=2, decode_responses=True
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis 初始化失败，使用内存缓存: %s", exc)
            _redis_client = None
    return _redis_client


async def cache_get(key: str) -> str | None:
    """读取缓存值，未命中返回 None。"""
    client = await _get_redis()
    if client is not None:
        try:
            value = await client.get(key)
            return str(value) if value is not None else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis 读取失败，退化为内存缓存: %s", exc)
    entry = _memory_store.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if expires_at < time.monotonic():
        _memory_store.pop(key, None)
        return None
    return value


async def cache_set(key: str, value: str, ttl: int = DEFAULT_TTL) -> None:
    """写入缓存值（带 TTL 秒）。"""
    client = await _get_redis()
    if client is not None:
        try:
            await client.set(key, value, ex=ttl)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis 写入失败，退化为内存缓存: %s", exc)
    _memory_store[key] = (time.monotonic() + ttl, value)


async def cache_delete(*keys: str) -> None:
    """按精确键删除缓存。"""
    client = await _get_redis()
    if client is not None:
        try:
            await client.delete(*keys)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis 删除失败: %s", exc)
    for key in keys:
        _memory_store.pop(key, None)


async def cache_delete_prefix(prefix: str) -> None:
    """按前缀删除缓存（如字典类型变更时失效该类型全部键）。"""
    client = await _get_redis()
    if client is not None:
        try:
            keys = [key async for key in client.scan_iter(match=f"{prefix}*")]
            if keys:
                await client.delete(*keys)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis 前缀删除失败: %s", exc)
    for key in [k for k in _memory_store if k.startswith(prefix)]:
        _memory_store.pop(key, None)


def clear_memory_cache() -> None:
    """清空进程内缓存（测试辅助）。"""
    _memory_store.clear()
