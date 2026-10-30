"""Excel 导出工具。"""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.utils import get_column_letter


def build_workbook(
    headers: list[str], rows: list[list[object]], sheet_title: str = "Sheet1"
) -> bytes:
    """根据表头与数据行生成 xlsx 文件字节。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_title
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    for index, header in enumerate(headers, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = max(12, len(str(header)) * 2)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
