"""结构化日志初始化。

输出带时间戳、级别、模块、请求追踪 ID 的结构化日志行，便于生产环境采集
（如 Loki / ELK）。未引入第三方依赖，使用标准库 logging + 自定义 Formatter。
"""

import logging
import sys
from datetime import UTC, datetime

from app.core.config import settings


class StructuredFormatter(logging.Formatter):
    """将日志记录格式化为单行结构化文本（key=value + message）。"""

    BASE_FIELDS = ("asctime", "levelname", "name", "trace_id")

    def format(self, record: logging.LogRecord) -> str:
        record.asctime = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S%z")
        trace_id = getattr(record, "trace_id", "-")
        parts = [
            f"timestamp={record.asctime}",
            f"level={record.levelname}",
            f"logger={record.name}",
            f"trace_id={trace_id}",
            f"msg={record.getMessage()}",
        ]
        if record.exc_info:
            parts.append("error=" + self.formatException(record.exc_info).replace("\n", " "))
        return " ".join(parts)


_configured = False


def setup_logging() -> None:
    """配置根日志器（幂等），供应用启动时调用。"""
    global _configured
    if _configured:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.LOG_LEVEL.upper())

    # 抑制过于冗长的第三方日志
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    _configured = True
