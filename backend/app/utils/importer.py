"""Excel 导入解析工具（商品导入模板生成与解析）。"""

from io import BytesIO

from openpyxl import load_workbook

from app.utils.excel import build_workbook


IMPORT_HEADERS = [
    "商品编码",
    "商品名称",
    "规格",
    "分类名称",
    "单位名称",
    "条码",
    "成本价",
    "售价",
    "库存下限",
    "库存上限",
    "备注",
]

#: 导入模板首行说明
TEMPLATE_TIPS = [
    "",
    "",
    "",
    "不存在则自动创建",
    "不存在则自动创建",
    "",
    "",
    "",
    "",
    "",
    "",
]


def build_import_template() -> bytes:
    """生成商品导入模板（表头 + 一行示例）。"""
    rows = [
        TEMPLATE_TIPS,
        ["GD0001", "示例商品", "规格A", "默认分类", "个", "6901234567890", 10, 20, 5, 100, "示例行，可删除"],
    ]
    return build_workbook(IMPORT_HEADERS, rows, sheet_title="商品导入")


def parse_import_rows(content: bytes) -> list[tuple[int, dict[str, object]]]:
    """解析上传的 xlsx 内容。

    返回 (Excel 行号, 按 IMPORT_HEADERS 映射的字典) 列表，不含表头与空行。
    """
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    try:
        sheet = workbook.active
        if sheet is None:
            return []
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            return []
        header_index = _map_headers(rows[0])
        result: list[tuple[int, dict[str, object]]] = []
        for excel_row, row in enumerate(rows[1:], start=2):
            item = {
                name: (row[idx] if idx < len(row) else None)
                for name, idx in header_index.items()
            }
            # 整行为空则跳过
            if all(value is None or str(value).strip() == "" for value in item.values()):
                continue
            result.append((excel_row, item))
        return result
    finally:
        workbook.close()


def _map_headers(header_row: tuple[object, ...]) -> dict[str, int]:
    """建立表头名称到列索引的映射，支持前后空白。"""
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(header_row):
        name = str(cell).strip() if cell is not None else ""
        if name:
            mapping.setdefault(name, idx)
    return mapping


def to_str(value: object) -> str:
    """单元格转字符串（None -> ''）。"""
    if value is None:
        return ""
    return str(value).strip()


def to_float(value: object, default: float = 0) -> float:
    """单元格转浮点，失败回退默认值。"""
    if value is None or value == "":
        return default
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip().replace(",", "")
    try:
        return float(text)
    except ValueError:
        return default
