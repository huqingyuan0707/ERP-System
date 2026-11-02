"""请求审计上下文与操作日志写入。"""

import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.models.sys import SysOperLog
from app.repositories.oper_log import OperLogRepository


logger = logging.getLogger(__name__)
_SENSITIVE = {
    "password",
    "oldpassword",
    "newpassword",
    "token",
    "accesstoken",
    "refreshtoken",
    "authorization",
    "secret",
    "idcard",
    "account",
}


@dataclass
class AuditContext:
    method: str
    url: str
    ip: str | None
    started: float
    params: dict[str, Any] = field(default_factory=dict)
    tenant_id: int | None = None
    operator_id: int | None = None


_context: ContextVar[AuditContext | None] = ContextVar("audit_context", default=None)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***" if str(key).replace("_", "").lower() in _SENSITIVE else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def bind_audit_actor(tenant_id: int, operator_id: int) -> None:
    """只由认证完成后的依赖绑定，不能使用客户端声明的租户。"""
    context = _context.get()
    if context is not None:
        context.tenant_id = tenant_id
        context.operator_id = operator_id


async def write_oper_log(
    db: AsyncSession,
    *,
    tenant_id: int,
    operator_id: int,
    action: str,
    params: dict[str, Any] | None = None,
    module: str = "基础资料",
    status: int = 1,
    result: str = "success",
) -> None:
    """只写入当前事务；业务调用方负责成功提交或失败回滚。"""
    context = _context.get()
    details = dict(context.params) if context else {}
    if params:
        details.update(params)
    await OperLogRepository(db, tenant_id=tenant_id).add(
        SysOperLog(
            tenant_id=tenant_id,
            user_id=operator_id,
            module=module,
            action=action,
            method=context.method if context else None,
            url=context.url if context else None,
            ip=context.ip if context else None,
            params=json.dumps(_redact(details), ensure_ascii=False, default=str),
            result=result,
            duration_ms=max(0, int((perf_counter() - context.started) * 1000)) if context else 0,
            status=status,
        )
    )


class AuditMiddleware(BaseHTTPMiddleware):
    """为日志提供请求元数据；失败记录在业务事务回滚后单独保存。"""

    async def dispatch(self, request: Request, call_next):
        context = AuditContext(
            method=request.method,
            url=request.url.path,
            ip=request.client.host if request.client else None,
            started=perf_counter(),
            params={"query": dict(request.query_params)} if request.query_params else {},
        )
        token = _context.set(context)
        try:
            if "application/json" in request.headers.get("content-type", ""):
                length = request.headers.get("content-length", "")
                if length.isdigit() and int(length) <= 65536:
                    try:
                        body = await request.json()
                        if isinstance(body, dict):
                            context.params["body"] = _redact(body)
                    except (ValueError, UnicodeError):
                        pass
            try:
                response = await call_next(request)
            except Exception:
                await self._record_failure(context, 500)
                raise
            if response.status_code >= 400:
                await self._record_failure(context, response.status_code)
            return response
        finally:
            _context.reset(token)

    @staticmethod
    async def _record_failure(context: AuditContext, status_code: int) -> None:
        is_operation = context.method in {"POST", "PUT", "PATCH", "DELETE"} or context.url.endswith(
            "/export"
        )
        if not is_operation or "/base/" not in context.url:
            return
        if context.tenant_id is None or context.operator_id is None:
            return
        try:
            from app.db.session import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                await write_oper_log(
                    db,
                    tenant_id=context.tenant_id,
                    operator_id=context.operator_id,
                    action=f"{context.method} {context.url}"[:64],
                    status=0,
                    result=f"HTTP {status_code}",
                )
                await db.commit()
        except Exception:
            logger.exception("写入失败操作日志失败：%s %s", context.method, context.url)
