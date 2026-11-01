"""Excel 导出工具。"""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.utils import get_column_letter


def build_workbook(
    headers: list[str], rows: list[list[object]], sheet_title: str = "Sheet1"
) -> bytes:
    """根据表头与数据行生成 xlsx 文件字节。"""
    workbook = Workbook()
    # 用 create_sheet 显式新建工作表：它的返回值类型就是 Worksheet（不是 Optional），
    # 而 Workbook.active 在 openpyxl 的类型存根里被标成可选值，会让静态检查无法推断。
    sheet = workbook.create_sheet(title=sheet_title)
    # 移除 Workbook() 自带的默认空表（名为 Sheet），避免导出文件里多出一张空表
    workbook.remove(workbook["Sheet"])
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    for index, header in enumerate(headers, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = max(12, len(str(header)) * 2)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
