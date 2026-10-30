"""单号生成：``前缀 + yyyyMMdd + N 位序号``，按“前缀 + 日期”每日重置。

规则存 ``sys_config``，键为 ``billno.{biz_key}``，值为 ``前缀,序号宽度``（如 ``PO,4``）。
序号写入 ``sys_bill_no_seq``（biz_key + period 唯一），UPSERT 递增保证并发下不重复；
业务表单号字段的唯一索引作为最终兜底，插入冲突时由业务方重试。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_get, cache_set
from app.core.exceptions import AppException, ErrorCode
from app.models.sys import SysBillNoSeq, SysConfig

BILLNO_KEY_PREFIX = "billno."
BILLNO_DEFAULT_WIDTH = 4
BILLNO_MAX_WIDTH = 8

_DIALECT_INSERTS: dict[str, Any] = {"sqlite": sqlite.insert, "postgresql": postgresql.insert}


def parse_billno_rule(value: str | None, biz_key: str) -> tuple[str, int]:
    """解析单号规则 ``前缀,宽度``；非法规则抛 422。

    未配置时使用默认规则（大写业务键 + 默认宽度 4）。
    """
    if value is None or not value.strip():
        return biz_key.upper(), BILLNO_DEFAULT_WIDTH
    parts = value.split(",")
    prefix = parts[0].strip()
    if not prefix:
        raise AppException(ErrorCode.VALIDATION_ERROR, f"单号规则 {biz_key} 缺少前缀")
    if len(prefix) > 16:
        raise AppException(ErrorCode.VALIDATION_ERROR, f"单号规则 {biz_key} 前缀过长")
    width = BILLNO_DEFAULT_WIDTH
    if len(parts) > 1:
        if not parts[1].strip().isdigit():
            raise AppException(ErrorCode.VALIDATION_ERROR, f"单号规则 {biz_key} 序号宽度非法")
        width = int(parts[1].strip())
        if not 1 <= width <= BILLNO_MAX_WIDTH:
            raise AppException(
                ErrorCode.VALIDATION_ERROR,
                f"单号规则 {biz_key} 序号宽度须在 1~{BILLNO_MAX_WIDTH} 之间",
            )
    return prefix, width


async def _load_rule(db: AsyncSession, biz_key: str) -> tuple[str, int]:
    """读取单号规则（内存缓存 5 分钟，维护保存后失效）。"""
    key = f"cfg:{BILLNO_KEY_PREFIX}{biz_key}"
    cached = await cache_get(key)
    if cached is not None:
        return parse_billno_rule(cached, biz_key)
    value = await db.scalar(
        select(SysConfig.value).where(
            SysConfig.key == f"{BILLNO_KEY_PREFIX}{biz_key}", SysConfig.is_deleted.is_(False)
        )
    )
    result = parse_billno_rule(value, biz_key)
    await cache_set(key, value if value is not None else "")
    return result


async def generate_bill_no(db: AsyncSession, biz_key: str) -> str:
    """生成业务单号，如 ``PO202610300001``。

    序号递增使用独立连接立即提交：即使调用方业务事务回滚也允许跳号，
    但保证并发下序号不重复（需求口径：允许跳号、不允许重复）。
    """
    prefix, width = await _load_rule(db, biz_key)
    period = datetime.now().strftime("%Y%m%d")

    engine = db.bind
    if engine is None:
        raise AppException(ErrorCode.SERVER_ERROR, "数据库引擎未初始化")
    dialect_name = engine.dialect.name
    insert_fn = _DIALECT_INSERTS.get(dialect_name, sqlite.insert)
    stmt = insert_fn(SysBillNoSeq).values(biz_key=biz_key, period=period, current_seq=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=["biz_key", "period"], set_={"current_seq": SysBillNoSeq.current_seq + 1}
    ).returning(SysBillNoSeq.current_seq)

    async with engine.connect() as conn:
        seq = (await conn.execute(stmt)).scalar_one()
        await conn.commit()
    return f"{prefix}{period}{seq:0{width}d}"
