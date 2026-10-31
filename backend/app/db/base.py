"""数据库基础：DeclarativeBase 与全量模型注册。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


# 导入全部模型，确保 Base.metadata 完整（供 Alembic autogenerate 与建表使用）
from app.models import base_data, inventory, sys  # noqa: E402, F401


__all__ = ["Base", "base_data", "sys"]
