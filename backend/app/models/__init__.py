"""所有模型包。

统一导入各模块模型，保证 Base.metadata 注册到全部表，
alembic autogenerate 依赖本文件被 import 后的完整 metadata。
"""

from app.models import base_data, finance, idempotency, inventory, pur, sal, sys


__all__ = ["base_data", "finance", "idempotency", "inventory", "pur", "sal", "sys"]
