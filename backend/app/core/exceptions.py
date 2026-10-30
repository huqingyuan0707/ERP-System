"""统一异常与错误码，以及全局异常处理器。"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


logger = logging.getLogger("app.exception")


class ErrorCode:
    """业务错误码，0 表示成功。"""

    SUCCESS = 0
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    CONFLICT = 409
    VALIDATION_ERROR = 422
    TOO_MANY_REQUESTS = 429
    SERVER_ERROR = 500


class AppException(Exception):
    """业务异常，由全局处理器转换为统一响应。"""

    def __init__(self, code: int, message: str, data: object | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _http_status(code: int) -> int:
    """业务码即为 HTTP 状态时直接透传，否则回退 400。"""
    return code if 400 <= code <= 599 else ErrorCode.BAD_REQUEST


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(
        status_code=_http_status(exc.code),
        content={"code": exc.code, "message": exc.message, "data": exc.data},
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = exc.errors()
    message = "参数校验失败"
    if errors:
        first = errors[0]
        loc = [str(item) for item in first.get("loc", []) if item not in ("body", "query", "path")]
        detail = first.get("msg", message)
        message = f"{'.'.join(loc)}: {detail}" if loc else detail
    return JSONResponse(
        status_code=ErrorCode.VALIDATION_ERROR,
        content={"code": ErrorCode.VALIDATION_ERROR, "message": message, "data": None},
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """将 404/405 等 Starlette HTTP 异常统一为 {code,message,data}。

    401/403 已在鉴权依赖中以 AppException 抛出，这里兜底其它状态码。
    """
    code = exc.status_code
    if code == ErrorCode.NOT_FOUND:
        message = "请求的资源不存在"
    elif code == ErrorCode.UNAUTHORIZED:
        message = "未认证或登录已失效"
    elif code == ErrorCode.FORBIDDEN:
        message = "没有访问权限"
    else:
        message = str(exc.detail) if exc.detail else "请求处理失败"
    return JSONResponse(
        status_code=code,
        content={"code": code, "message": message, "data": None},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底未捕获异常，避免泄露堆栈，返回 500 统一结构。"""
    logger.exception("未处理的服务器异常: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=ErrorCode.SERVER_ERROR,
        content={"code": ErrorCode.SERVER_ERROR, "message": "服务器内部错误", "data": None},
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
