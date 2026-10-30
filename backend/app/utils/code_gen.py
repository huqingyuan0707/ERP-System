"""编码生成：按前缀 + 序号生成业务编码，如 GD0001。"""

DEFAULT_WIDTH = 4


def format_code(prefix: str, seq: int, width: int = DEFAULT_WIDTH) -> str:
    """按前缀与序号生成编码。"""
    return f"{prefix}{seq:0{width}d}"


def next_seq_from_code(code: str | None, prefix: str) -> int:
    """从已有最大编码解析下一个序号。"""
    if not code or not code.startswith(prefix):
        return 1
    suffix = code[len(prefix) :]
    return int(suffix) + 1 if suffix.isdigit() else 1
