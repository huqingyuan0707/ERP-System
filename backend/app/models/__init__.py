"""所有模型包。

统一导入各模块模型，保证 Base.metadata 注册到全部表，
alembic autogenerate 依赖 db.base 被 import 后的完整 metadata。
本文件与 db/base.py 共同维护模型导入清单，二者独立导入，不互相依赖，
避免循环导入。
"""

from app.models import base_data, finance, idempotency, inventory, pur, sal, sys  # noqa: F401


__all__ = ["base_data", "finance", "idempotency", "inventory", "pur", "sal", "sys"]
