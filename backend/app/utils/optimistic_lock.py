"""单据乐观锁：用条件 UPDATE 原子抢占 version，未命中即 409。

`VersionMixin` 只是普通 Integer 列（没有 SQLAlchemy 的 version_id_col），
所以 `entity.version += 1` 这种内存自增并不能阻止并发覆盖：两个事务各自
读到同一版本、各自 +1，后提交者会静默覆盖先提交者。这里改为
`UPDATE ... WHERE version = 读到的值`，由数据库判定谁赢。
"""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ErrorCode


async def bump_version(
    db: AsyncSession,
    model: type,
    entity: object,
    *,
    tenant_id: int,
    expected: int | None,
    user_id: int,
) -> None:
    """校验期望版本并把 version 原子 +1；并发冲突抛 409。

    ``expected`` 为 None 时跳过「前端回传版本」的比对，但仍执行条件 UPDATE：
    SQLite 没有 SELECT ... FOR UPDATE，这一步是状态流转防并发的必要兜底。
    ``synchronize_session=False`` 避免 ORM 对象被同步成新版本后回写覆盖。
    """
    current = int(entity.version)
    if expected is not None and expected != current:
        raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")
    result = await db.execute(
        update(model)
        .where(
            model.id == entity.id,
            model.tenant_id == tenant_id,
            model.version == current,
        )
        .values(version=current + 1, updated_by=user_id)
        .execution_options(synchronize_session=False)
    )
    if int(result.rowcount or 0) != 1:
        raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")
    # 保持 ORM 对象与数据库一致，避免后续 flush 把旧版本写回去，
    # 同时让本次响应里的 version 是新值。
    entity.version = current + 1
