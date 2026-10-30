"""通用 Schema：统一响应、分页、camelCase 别名。"""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field


T = TypeVar("T")

# 分页上限：需求约定 1<=pageSize<=100，超出直接拒绝（422）。
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


def to_camel(snake_str: str) -> str:
    """snake_case 字段名转换为 camelCase 别名。"""
    parts = snake_str.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


class ApiResponse(BaseModel, Generic[T]):
    """统一响应体 {code, message, data}，字段以 camelCase 对外。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    code: int = 0
    message: str = "success"
    data: T | None = None


class PageResult(BaseModel, Generic[T]):
    """分页结果 {total, page, pageSize, items}，对外字段 camelCase。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    total: int = 0
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE
    items: list[T] = Field(default_factory=list)


class PageQuery(BaseModel):
    """分页查询参数（对外 query 参数名用 camelCase，如 pageSize）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    keyword: str | None = None


def success_response(
    data: Any | None = None, message: str = "success"
) -> ApiResponse[Any]:
    """构造成功响应。"""
    return ApiResponse(code=0, message=message, data=data)
