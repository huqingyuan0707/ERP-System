"""系统参数服务：查询、批量保存（含单号规则校验）、缓存失效。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete, cache_get, cache_set
from app.core.exceptions import AppException, ErrorCode
from app.models.sys import SysConfig
from app.repositories.config import ConfigRepository
from app.schemas.config import ConfigUpdateItem
from app.services.audit import write_oper_log
from app.utils.bill_no import BILLNO_KEY_PREFIX, parse_billno_rule


CONFIG_CACHE_PREFIX = "cfg:"
CONFIG_CACHE_ALL = "configs:all"

# 页面可维护的参数白名单（需求条目拆分.md S-03）。
# 不在名单内的键既不会出现在 GET /sys/configs 响应里，也不能从页面修改，
# 避免 JWT_SECRET、数据库口令等敏感配置被读取或篡改。
PUBLIC_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "company.name",
        "company.address",
        "company.phone",
        "sales.credit.mode",
        "billno.pur_apply",
        "billno.pur_order",
        "billno.pur_inbound",
        "billno.pur_return",
        "billno.sal_quotation",
        "billno.sal_order",
        "billno.sal_outbound",
        "billno.sal_return",
        "billno.inv_transfer",
        "billno.inv_check",
        "billno.fin_receipt",
        "billno.fin_payment",
        "billno.fin_expense",
    }
)


def is_public_config_key(key: str) -> bool:
    """是否属于允许页面读取/维护的参数。"""
    return key in PUBLIC_CONFIG_KEYS


class ConfigService:
    """系统参数业务逻辑。"""

    def __init__(self, db: AsyncSession, *, auto_commit: bool = True) -> None:
        self._db = db
        self._repo = ConfigRepository(db)
        self._auto_commit = auto_commit

    async def _flush_or_commit(self) -> None:
        """端点用 IdempotencyService 包装时关闭自动提交，由它统一提交业务与幂等记录。"""
        if self._auto_commit:
            await self._db.commit()
        else:
            await self._db.flush()

    async def list_all(self) -> list[SysConfig]:
        """页面可见参数：过滤掉白名单外的敏感配置，避免出现在接口响应里。"""
        rows = await self._repo.list_all()
        return [row for row in rows if is_public_config_key(row.key)]

    async def get_value(self, key: str, default: str | None = None) -> str | None:
        """读取单个参数值（带缓存），缺失返回默认值。"""
        cache_key = f"{CONFIG_CACHE_PREFIX}{key}"
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached or default
        entity = await self._repo.get_by_key(key)
        value = entity.value if entity is not None else None
        await cache_set(cache_key, value or "")
        return value if value not in (None, "") else default

    async def batch_update(
        self,
        items: list[ConfigUpdateItem],
        *,
        operator_id: int | None = None,
        tenant_id: int | None = None,
    ) -> list[SysConfig]:
        """批量保存：白名单校验、规则校验、修改前后值留痕、缓存失效。

        格式校验放在白名单校验之前，保证非法单号规则仍返回 422（S-03 验收项）；
        操作日志由当前事务提交，业务回滚时日志一并撤销。
        """
        for item in items:
            if item.key == "sales.credit.mode" and item.value not in {"block", "warn", "off"}:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR,
                    "sales.credit.mode 仅支持 block、warn、off",
                )
            if item.key.startswith(BILLNO_KEY_PREFIX):
                parse_billno_rule(item.value, item.key.removeprefix(BILLNO_KEY_PREFIX))
        updated: list[SysConfig] = []
        changes: list[dict[str, str | None]] = []
        for item in items:
            if not is_public_config_key(item.key):
                raise AppException(
                    ErrorCode.FORBIDDEN,
                    f"参数 {item.key} 不在可维护白名单内，不允许通过页面修改",
                )
            entity = await self._repo.get_by_key(item.key)
            if entity is None:
                raise AppException(ErrorCode.NOT_FOUND, f"参数 {item.key} 不存在")
            if entity.value != item.value:
                changes.append({"key": item.key, "old": entity.value, "new": item.value})
            entity.value = item.value
            updated.append(entity)
        if changes and operator_id is not None and tenant_id is not None:
            await write_oper_log(
                self._db,
                tenant_id=tenant_id,
                operator_id=operator_id,
                action="修改系统参数",
                module="系统管理",
                params={"changes": changes},
            )
        await self._flush_or_commit()
        for entity in updated:
            await cache_delete(f"{CONFIG_CACHE_PREFIX}{entity.key}")
        await cache_delete(CONFIG_CACHE_ALL)
        return updated
