"""操作日志数据访问。"""

from app.models.sys import SysOperLog
from app.repositories.base import BaseRepository


class OperLogRepository(BaseRepository[SysOperLog]):
    """操作日志仓储。"""

    model = SysOperLog
