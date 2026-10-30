"""分页工具。"""

from typing import TypeVar

from app.schemas.common import PageResult


ItemT = TypeVar("ItemT")


def calc_offset(page: int, page_size: int) -> int:
    """计算 SQL 偏移量。"""
    return (max(page, 1) - 1) * page_size


def paginate_result(
    items: list[ItemT], total: int, page: int, page_size: int
) -> PageResult[ItemT]:
    """组装分页结果。"""
    return PageResult(total=total, page=page, page_size=page_size, items=items)
